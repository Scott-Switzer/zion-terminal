"""Versioned companion SEC Company Facts source for the equity claims route.

The SEC engine release is a *normalized* projection: ``normalize_sources`` keeps only the
concepts its metric registry publishes, so its members carry ``observations``, not the
Company Facts ``{taxonomy: {tag: {units: {unit: [rows]}}}}`` shape a balance-sheet claims
extraction needs. Rather than mutate an immutable published release or widen the engine
registry, the facts live in their own immutable release, derived from the same pinned
public SEC bulk archive.

Provenance chain, verifiable end to end and never rewritten in place:

    bulk archive object (bucket, key, etag, declared sha256, snapshot date)
      -> zip member (name, offset, compressed/uncompressed bytes, crc32)
      -> companion member bytes (sha256)
      -> the engine catalog entry's own ``materialized.member_sha256`` for the same CIK

That last link is what makes this a bundle rather than a second opinion: the engine
release already pinned the digest of the raw member it was normalized from, so the route
can require the two independently published releases to name the same bytes.

Nothing here fetches SEC at request time, accepts a caller-supplied object key, or reads
outside the companion release prefix.
"""

import json
import hashlib
import re

from private_financials import PrivateFinancialsError

SCHEMA_VERSION = "sec-companyfacts-source-v1"
PREFIX = "gold/sec-companyfacts/releases/"
RIGHTS = "PUBLIC_SEC_ONLY"
MAX_MANIFEST = 1048576
MAX_MEMBER = 16 * 1048576
SCOPE = "CONSOLIDATED_PUBLIC_SEC_COMPANY_FACTS"


def fail(code, status=503):
    raise PrivateFinancialsError(code, code, status)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


async def _pinned(env, key, digest, maximum):
    """Read one object only if the reference names this prefix and re-hashes to its digest."""
    if (
        not isinstance(key, str)
        or not key.startswith(PREFIX)
        or any(part in ("", ".", "..") for part in key.split("/"))
        or not isinstance(digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
    ):
        fail("SOURCE_REFERENCE_INVALID", 503)
    obj = await env.MARKET_DATA.get(key)
    if not obj or obj.size > maximum:
        fail("SOURCE_SIZE_BUDGET_OR_MISSING", 503)
    raw = (await obj.text()).encode()
    if len(raw) > maximum or sha(raw) != digest:
        fail("SOURCE_VERSION_CHANGED", 409)
    return raw


def _validate(m, digest):
    if (
        m.get("schema_version") != SCHEMA_VERSION
        or m.get("rights") != RIGHTS
        or m.get("licensed_sources_allowed") is not False
        or m.get("scope") != SCOPE
    ):
        fail("SOURCE_POLICY_NOT_PERMITTED", 403)
    if not isinstance(m.get("members"), dict) or not m["members"]:
        fail("COMPANY_FACTS_SOURCE_INVALID", 503)
    if not isinstance(m.get("release_id"), str) or not re.fullmatch(r"[a-f0-9]{64}", m["release_id"]):
        fail("COMPANY_FACTS_SOURCE_INVALID", 503)
    m["_manifest_sha256"] = digest
    return m


async def root(env):
    """The pinned companion release named by this deployment's configuration."""
    key = getattr(env, "SEC_COMPANYFACTS_MANIFEST_KEY", "")
    digest = getattr(env, "SEC_COMPANYFACTS_MANIFEST_SHA256", "")
    raw = await _pinned(env, key, digest, MAX_MANIFEST)
    return _validate(json.loads(raw), digest)


async def approved(env, current, release_id):
    """An explicitly named companion release the current one still lists as approved."""
    if release_id == current["release_id"]:
        return current
    if not isinstance(release_id, str) or not re.fullmatch(r"[a-f0-9]{64}", release_id):
        fail("INVALID_SOURCE_RELEASE_ID", 422)
    ref = (current.get("previous_releases") or {}).get(release_id)
    if not isinstance(ref, dict):
        fail("SOURCE_RELEASE_NOT_APPROVED", 404)
    raw = await _pinned(env, ref.get("key"), ref.get("sha256"), MAX_MANIFEST)
    value = _validate(json.loads(raw), ref["sha256"])
    if value["release_id"] != release_id:
        fail("SOURCE_POLICY_NOT_PERMITTED", 403)
    return value


async def member(env, manifest, cik):
    """The Company Facts member for one filer, verified against its pinned digest."""
    ref = manifest["members"].get(cik)
    if not isinstance(ref, dict):
        fail("COMPANY_FACTS_MEMBER_NOT_IN_SOURCE", 404)
    if ref.get("sha256") != ref.get("engine_member_sha256"):
        # The companion release is only publishable when it names the same bytes the
        # engine release pinned; a discrepancy means the release was assembled wrongly.
        fail("SOURCE_BUNDLE_MEMBER_MISMATCH", 409)
    raw = await _pinned(env, ref.get("key"), ref.get("sha256"), MAX_MEMBER)
    data = json.loads(raw)
    if str(data.get("cik")).zfill(10) != cik or not isinstance(data.get("facts"), dict):
        fail("SOURCE_CONTEXT_INVALID", 503)
    return data, ref
