"""Snapshot metadata only. Never writes source data or replaces source release IDs."""
from __future__ import annotations

from contextvars import ContextVar

CURRENT_SNAPSHOT = ContextVar("snapshot_bundle", default=None)
RESPONSE_DATA_AS_OF = ContextVar("snapshot_coverage", default=None)

import hashlib
import json
import re
from datetime import date

SCHEMA = 'zion-snapshot-bundle-v1'
# Explicit inventory: add new serving adapters here when they gain a release pin.
RELEASE_KEYS = ('SEC_EXPANDED_INDEX_KEY', 'SEC_HISTORY_MANIFEST_KEY', 'SEC_ENGINE_MANIFEST_KEY',
                'SEC_READINESS_INDEX_KEY', 'SEC_COMPANYFACTS_MANIFEST_KEY',
                'UNIFIED_RESEARCH_MANIFEST_KEY', 'STOCK_HISTORY_KEY', 'CRSP_HISTORY_KEY',
                'HISTORICAL_SERIES_KEY', 'HISTORICAL_COVERAGE_KEY')
BASE = 'staging/security-funnel/2026-10-20/9beaa712e71650a2/'


def bundle_id(sources: dict) -> str:
    if not sources or any(not isinstance(v, str) or not v for v in sources.values()):
        raise ValueError('BUNDLE_UNAVAILABLE')
    canonical = json.dumps({'schema_version': SCHEMA, 'source_releases': sources}, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        date.fromisoformat(value)
        return value
    except ValueError:
        return None


def data_as_of(value) -> str | None:
    """Latest returned observation, not retrieval/publication time or forecast horizon."""
    dates = []
    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for key in ('period_end', 'session_date', 'session', 'date', 'end_date', 'price_as_of', 'period'):
                if valid_date(node.get(key)):
                    dates.append(node[key])
            for key in ('data', 'result', 'observations', 'prices', 'rows', 'evidence', 'series', 'historical', 'financials', 'market', 'metric', 'metrics', 'structuredContent'):
                if key in node:
                    visit(node[key])
    visit(value)
    return max(dates) if dates else None


def assert_bundle(expected, snapshot):
    if expected is not None and expected != snapshot['bundle_id']:
        raise ValueError('RELEASE_MISMATCH')


def stamp(value, snapshot):
    if not isinstance(value, dict):
        return value
    assert_bundle(value.get('bundle_id'), snapshot)
    release = value.get('release') or {}
    served = value.get('serving_release_id') or (release.get('serving_release_id') if isinstance(release, dict) else None)
    if served is not None and served != snapshot['source_releases'].get('serving'):
        raise ValueError('RELEASE_MISMATCH')
    return {**value, 'bundle_id': snapshot['bundle_id'], 'data_as_of': data_as_of(value),
            'source_releases': snapshot['source_releases']}


async def _reference(env, key, *, optional=False):
    obj = await env.MARKET_DATA.get(key)
    if obj is None and optional:
        return None
    if obj is None or obj.size > 2 * 1048576:
        raise ValueError('BUNDLE_REFERENCE_UNAVAILABLE')
    raw = (await obj.text()).encode()
    if len(raw) > 2 * 1048576:
        raise ValueError('BUNDLE_REFERENCE_UNAVAILABLE')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


async def load_snapshot(env):
    from serving_v2 import load_release
    _, manifest = await load_release(env, pointer_ttl_ms=0)
    sources = {'serving': manifest['serving_release_id']}
    for name, source in manifest.get('source', {}).items():
        if isinstance(source, dict):
            identity = source.get('release_id') or source.get('snapshot_id')
            if identity:
                sources['serving/' + name] = str(identity)
    for setting in RELEASE_KEYS:
        key = getattr(env, setting, '')
        if not key:
            continue
        match = re.search(r'/(?:releases|index|coverage)/([a-f0-9]{32,64})(?:/|\.json$)', key)
        if not match:
            raise ValueError('BUNDLE_REFERENCE_INVALID')
        sources[setting] = match[1]
        digest = getattr(env, setting.removesuffix('_KEY') + '_SHA256', '')
        if digest:
            sources[setting + '/manifest_sha256'] = digest
    # The shared question API reads these existing materializations in the same bucket.
    # Include both their source pins and reference digests; no payload is acquired.
    if str(getattr(env, 'BUNDLE_SHARED_SNAPSHOT', 'false')).lower() == 'true':
        for name, key, field in (
            ('shared_history', BASE + 'full-history-2026-10-21-v1/plan.json', 'input_sha256'),
            ('shared_sec_packets', BASE + 'source-enrichment-2026-10-21-v1/job.json', 'run_id'),
        ):
            value, digest = await _reference(env, key)
            if not isinstance(value.get(field), str) or not value[field]:
                raise ValueError('BUNDLE_REFERENCE_INVALID')
            sources[name] = value[field]
            sources[name + '/reference_sha256'] = digest
    daily = await _reference(env, 'control/daily-prices/CURRENT.json', optional=True)
    if daily is None and str(getattr(env, 'BUNDLE_SHARED_SNAPSHOT', 'false')).lower() == 'true':
        capabilities, _ = await _reference(env, BASE + 'existing-capabilities.json')
        pointer = capabilities['daily_pointer']
    else:
        pointer = daily[0] if daily else None
    if pointer is not None:
        digest = pointer.get('sha256', '')
        if not re.fullmatch(r'[a-f0-9]{64}', digest) or pointer.get('data_key') != f'gold/daily-prices/releases/{digest}/data.json':
            raise ValueError('BUNDLE_REFERENCE_INVALID')
        sources['daily_prices'] = digest
    return {'schema_version': SCHEMA, 'bundle_id': bundle_id(sources),
            'source_releases': dict(sorted(sources.items())), 'data_as_of': None}


def stamp_current(value):
    snapshot = CURRENT_SNAPSHOT.get()
    result = stamp(value, snapshot) if snapshot else {**value, 'bundle_id': None, 'data_as_of': None, 'source_releases': {}} if isinstance(value, dict) else value
    RESPONSE_DATA_AS_OF.set(result.get('data_as_of') if isinstance(result, dict) else None)
    return result
