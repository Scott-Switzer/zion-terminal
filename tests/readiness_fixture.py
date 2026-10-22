"""Read-only validation of explicitly selected, immutable readiness audit fixtures."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path

MAX_TOTAL_BYTES = 32 * 1048576
MAX_PART_BYTES = 16 * 1048576
MAX_METADATA_BYTES = 2 * 1048576
MAX_DECOMPRESSED_PART_BYTES = 64 * 1048576


def fail(kind='INTEGRITY'):
    raise ValueError('READINESS_FIXTURE_' + kind)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(path: Path, limit: int):
    if not path.is_file():
        fail('INCOMPLETE')
    if path.is_symlink() or path.stat().st_size > limit:
        fail()
    raw = path.read_bytes()
    if len(raw) > limit:
        fail()
    return raw


def validate_receipt(receipt, expected_release_id):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_release_id or ''):
        fail()
    prefix = f'gold/sec-readiness/releases/{expected_release_id}/'
    if receipt.get('release_id') != expected_release_id or receipt.get('prefix') != prefix or receipt.get('bucket') != 'financial-system-datasets':
        fail()
    references = {}
    artifacts = receipt.get('artifacts')
    if not isinstance(artifacts, list) or not 2 <= len(artifacts) <= 514:
        fail()
    for item in artifacts:
        key = item.get('key', '')
        name = key.removeprefix(prefix)
        if not key.startswith(prefix) or not re.fullmatch(r'(?:part-\d{4,6}\.jsonl\.gz|manifest\.json|summary\.json)', name) or name in references:
            fail()
        size = item.get('bytes')
        if type(size) is not int or not 0 < size <= MAX_PART_BYTES or not re.fullmatch(r'[a-f0-9]{64}', item.get('sha256', '')):
            fail()
        references[name] = item
    if 'manifest.json' not in references or sum(v['bytes'] for v in references.values()) > MAX_TOTAL_BYTES:
        fail()
    return references


def verified_file(path, ref):
    raw = read_file(path, ref['bytes'])
    if len(raw) != ref['bytes'] or digest(raw) != ref['sha256']:
        fail()
    return raw


def manifest_parts(manifest, expected_release_id, references):
    if manifest.get('release_id') != expected_release_id or type(manifest.get('issuers')) is not int or manifest['issuers'] <= 0:
        fail()
    parts = manifest.get('parts')
    if not isinstance(parts, list) or not parts:
        fail()
    seen = set()
    for part in parts:
        name = part.get('part', '')
        if not re.fullmatch(r'part-\d{4,6}\.jsonl\.gz', name) or name in seen or name not in references:
            fail()
        if type(part.get('issuers')) is not int or part['issuers'] <= 0:
            fail()
        if any(part.get(k) != references[name][k] for k in ('bytes', 'sha256')):
            fail()
        seen.add(name)
    if sum(p['issuers'] for p in parts) != manifest['issuers'] or seen != {n for n in references if n.startswith('part-')}:
        fail()
    return parts


def iter_readiness_records(release_dir, manifest):
    for part in manifest['parts']:
        total = 0
        try:
            with gzip.open(Path(release_dir) / part['part'], 'rb') as handle:
                while line := handle.readline(1048576 + 1):
                    total += len(line)
                    if len(line) > 1048576 or total > MAX_DECOMPRESSED_PART_BYTES:
                        fail()
                    if line.strip():
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            fail()
                        yield row
        except (OSError, EOFError, json.JSONDecodeError):
            fail()


def load_readiness_fixture(release_dir: Path, expected_release_id: str) -> dict:
    folder = Path(release_dir)
    receipt = json.loads(read_file(folder / 'publication.json', MAX_METADATA_BYTES))
    references = validate_receipt(receipt, expected_release_id)
    manifest = json.loads(verified_file(folder / 'manifest.json', references['manifest.json']))
    parts = manifest_parts(manifest, expected_release_id, references)
    if 'summary.json' in references:
        verified_file(folder / 'summary.json', references['summary.json'])
    seen = set()
    for part in parts:
        verified_file(folder / part['part'], references[part['part']])
        count = 0
        for row in iter_readiness_records(folder, {'parts':[part]}):
            cik = row.get('cik')
            if not isinstance(cik, str) or not re.fullmatch(r'\d{10}', cik) or cik in seen:
                fail()
            seen.add(cik)
            count += 1
        if count != part['issuers']:
            fail()
    if len(seen) != manifest['issuers']:
        fail()
    return manifest


def load_archive_context(release_dir: Path, manifest: dict) -> tuple[dict, dict]:
    folder = Path(release_dir)
    context = json.loads(read_file(folder / 'audit-context.json', MAX_METADATA_BYTES))
    engine_raw = read_file(folder / 'engine-manifest.json', MAX_PART_BYTES)
    index_raw = read_file(folder / 'member-index.json', MAX_PART_BYTES)
    if digest(engine_raw) != context.get('engine_sha256') or digest(index_raw) != context.get('member_index_sha256'):
        fail()
    engine, index = json.loads(engine_raw), json.loads(index_raw)
    if engine.get('release_id') != manifest.get('engine_release_id') or engine.get('source') != manifest.get('archive') or engine.get('date_to') != manifest.get('archive_cutoff'):
        fail()
    if not isinstance(index, dict) or not index:
        fail()
    return engine, index
