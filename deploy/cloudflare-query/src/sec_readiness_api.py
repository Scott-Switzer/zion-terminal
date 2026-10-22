"""Serve the readiness screening index: eligibility summaries and bounded screens.

The screening questions ("which issuers can actually be valued, and with what") are answered from
the derived index published by ``scripts/sec_readiness_index.py``, never from the record release
and never from the SEC archive. The index redistributes the same compact row into one artifact per
screening filter (a method, a flag, a missing-input code, a status) and one per CIK remainder
shard, so a request reads the pinned manifest -- which is where the counts come from -- and then
the single narrowest filter artifact it was given, and applies the remaining predicates in memory.

Nothing here decides eligibility. Every verdict in a response was decided by the recorded
classification run and is carried through unchanged, including the states that are easy to
flatten: a claim reviewed and found absent is reported as a reviewed absence, an issuer that could
not be classified is reported as unclassified rather than dropped, and no missing claim is served
as a zero.
"""

import asyncio
import json
import re
from urllib.parse import urlparse

from private_financials import PrivateFinancialsError
from private_http import authenticate
from sec_engine import encoded
from sec_engine_api import cursor_decode, cursor_encode, fail, parameters, sha
from sec_sector import IDENTIFIER_ABSENT_GROUPS, SECTOR_GROUPS

PREFIX = "gold/sec-readiness/index/"
VERSION = "zion-readiness-screen-index-v1"
MAX_MANIFEST = 1048576
MAX_FILTER_BYTES = 12 * 1048576
MAX_RESPONSE = 2 * 1048576
MAX_MATCHES = 500
MAX_CIKS = 2000
METHODS = ("operating_dcf", "fcfe", "apv", "bank_excess_return", "residual_income", "ddm",
           "comparables")
FLAGS = ("financial_history_available", "complete_equity_bridge", "dated_share_basis_verified",
         "any_implemented_method_eligible", "operating_dcf_eligible",
         "specialized_method_required")
PARAMETERS = {
    "method", "flag", "missing", "status", "cik", "ciks", "limit", "cursor", "view",
    "expected_index_id", "recipient", "sector", "share_absence",
}
# A sector filter is validated against the shared vocabulary rather than against what happens to
# be published, so a misspelled group is a bad request and not an empty answer. The share-basis
# absences are validated the way missing-input codes are: against the published buckets, because
# the reason codes are written by the classification run, not by this service.
SECTORS = tuple(sorted(SECTOR_GROUPS + IDENTIFIER_ABSENT_GROUPS))


def values(q, name, allowed=None):
    """A comma-separated repeated filter, validated against the published filter names."""
    raw = q.get(name)
    if not raw:
        return []
    items = raw.split(",")
    if len(items) != len(set(items)) or any(not re.fullmatch(r"[A-Za-z0-9_]{1,64}", item)
                                           for item in items):
        fail("INVALID_SCREEN_FILTER", 422)
    if allowed is not None and any(item not in allowed for item in items):
        fail("SCREEN_FILTER_NOT_PUBLISHED", 422)
    return items


async def object_at(env, ref, maximum):
    """Read one published index object, refusing anything outside its own declared identity."""
    key, digest = ref.get("key", ""), ref.get("sha256", "")
    if (
        not key.startswith(PREFIX)
        or any(part in ("", ".", "..") for part in key.split("/"))
        or not re.fullmatch("[a-f0-9]{64}", digest)
    ):
        fail("SOURCE_REFERENCE_INVALID", 503)
    obj = await env.MARKET_DATA.get(key)
    if not obj or obj.size > maximum:
        fail("SOURCE_SIZE_BUDGET_OR_MISSING", 503)
    raw = (await obj.text()).encode()
    if len(raw) > maximum or sha(raw) != digest:
        fail("SOURCE_VERSION_CHANGED", 409)
    return raw


async def manifest(env):
    ref = {
        "key": getattr(env, "SEC_READINESS_INDEX_KEY", ""),
        "sha256": getattr(env, "SEC_READINESS_INDEX_SHA256", ""),
    }
    raw = await object_at(env, ref, MAX_MANIFEST)
    m = json.loads(raw)
    if (
        m.get("schema_version") != VERSION
        or m.get("qualification") is None
        or not re.fullmatch("[a-f0-9]{64}", m.get("index_id", ""))
        or ref["key"] != PREFIX + m["index_id"] + "/manifest.json"
    ):
        fail("SOURCE_POLICY_NOT_PERMITTED", 403)
    m["_manifest_sha256"] = ref["sha256"]
    return m


def entry(m, name):
    """The declared artifact for a filter or shard name, refused when it is not published."""
    declared = (m.get("artifacts") or {}).get(name)
    if not isinstance(declared, dict):
        fail("SCREEN_FILTER_NOT_PUBLISHED", 404)
    size = declared.get("bytes")
    if type(size) is not int or not 0 < size <= MAX_FILTER_BYTES:
        fail("SCREEN_SOURCE_SIZE_INVALID", 409)
    return {"key": PREFIX + m["index_id"] + "/" + name, "sha256": declared.get("sha256"),
            "bytes": size, "issuers": declared.get("issuers")}


def published(m, name):
    """The declared artifact when the index has one, or None when that shard is simply empty.

    A filter that was never published is a question the index cannot answer. A shard that was
    never published is a remainder bucket with no issuers in it, which for a named issuer is an
    absence to report, not a filter to reject.
    """
    declared = (m.get("artifacts") or {}).get(name)
    return entry(m, name) if isinstance(declared, dict) else None


def narrowest(m, method, flags, missing, status, sectors, share_absence):
    """Choose the smallest complete candidate set: union within OR, intersection across filters."""
    groups = []
    for prefix, values in (("method", method), ("status", status),
                           ("sector", sectors), ("share_absence", share_absence)):
        if values:
            groups.append(["buckets/" + prefix + "-" + value + ".json" for value in values])
    groups += [["buckets/missing-" + value + ".json"] for value in missing]
    groups += [["buckets/flag-" + value + ".json"] for value in flags]
    artifacts = m.get("artifacts") or {}
    candidates = [(sum(artifacts[name].get("issuers", m["issuers"]) for name in names), names)
                  for names in groups if all(isinstance(artifacts.get(name), dict) for name in names)]
    if not candidates:
        fail("SCREEN_FILTER_NOT_PUBLISHED", 404)
    return min(candidates)[1]


def matches(item, method, flags, missing, status, sectors, share_absence):
    """Every supplied filter must hold, so filters narrow and never widen."""
    if method and not set(method) & set(item.get("methods") or []):
        return False
    if missing and not set(missing).issubset(set(item.get("missing") or [])):
        return False
    if flags and not set(flags).issubset(set(item.get("flags") or [])):
        return False
    if sectors and item.get("sector") not in sectors:
        return False
    if share_absence and item.get("share_absence") not in share_absence:
        return False
    return not (status and item.get("status") not in status)


async def execute(env, request):
    authenticate(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    q = parameters(request.url, PARAMETERS)
    if q.get("recipient", "human_research") != "human_research":
        fail("RECIPIENT_NOT_PERMITTED", 403)
    method = values(q, "method", METHODS)
    flags = values(q, "flag", FLAGS)
    missing = values(q, "missing")
    status = values(q, "status")
    sectors = values(q, "sector", SECTORS)
    share_absence = values(q, "share_absence")
    ciks = q["ciks"].split(",") if q.get("ciks") else ([q["cik"]] if q.get("cik") else [])
    if any(not re.fullmatch(r"\d{10}", cik) for cik in ciks):
        fail("INVALID_CIK", 422)
    if len(ciks) > MAX_CIKS or len(ciks) != len(set(ciks)):
        fail("INVALID_SCREEN_CIKS", 422)
    limit = int(q.get("limit", "25"))
    if not 1 <= limit <= MAX_MATCHES:
        fail("INVALID_SCREEN_PARAMETER", 422)
    view = q.get("view", "screen" if (method or flags or missing or status or ciks or sectors
                                      or share_absence) else "summary")
    if view not in ("summary", "screen"):
        fail("INVALID_SCREEN_PARAMETER", 422)
    m = await manifest(env)
    if q.get("expected_index_id", m["index_id"]) != m["index_id"]:
        fail("SOURCE_VERSION_CHANGED", 409)
    base = {
        "schema_version": VERSION,
        "index_id": m["index_id"],
        "release_id": m["record_release_id"],
        "record_manifest_sha256": (m.get("record_manifest") or {}).get("sha256"),
        "archive_cutoff": m.get("archive_cutoff"),
        "availability_cutoff": m.get("availability_cutoff"),
        "engine_release_id": m.get("engine_release_id"),
        "qualification": m["qualification"],
        "evidence_states": ["REPORTED", "REVIEWED_ABSENT", "UNAVAILABLE", "CONFLICTING"],
    }
    filtered = bool(method or flags or missing or status or sectors or share_absence)
    name = (narrowest(m, method, flags, missing, status, sectors, share_absence)
            if view == "screen" and filtered else None)
    if view == "summary" or not (name or ciks):
        if view == "screen":
            fail("SCREEN_REQUIRES_A_PUBLISHED_FILTER", 422)
        base.update(
            view="summary",
            issuers=m["issuers"],
            counts=m["counts"],
            filters={name.split("/")[-1][: -len(".json")]: {"issuers": declared["issuers"],
                                                           "bytes": declared["bytes"]}
                     for name, declared in sorted((m.get("artifacts") or {}).items())
                     if name.startswith("buckets/")},
            provenance={"index_manifest": PREFIX + m["index_id"] + "/manifest.json",
                        "index_manifest_sha256": m["_manifest_sha256"],
                        "record_manifest": (m.get("record_manifest") or {}).get("key"),
                        "record_release_verified_by_digest": True,
                        "licensed_sources_used": False},
            limits={"index_objects_read": 1, "archive_bytes_read": 0},
        )
        return base, 200

    refs = [entry(m, item) for item in name] if name else []
    if not refs:
        # A named issuer's row is reachable without reading a whole filter artifact: the shards
        # are the CIK remainder, so one requested issuer costs one shard.
        refs = [ref for ref in (published(m, "cik/%02d.json" % (int(cik) % 100))
                                for cik in ciks) if ref]
        refs = list({ref["key"]: ref for ref in refs}.values())
    declared_bytes = sum(ref["bytes"] for ref in refs)
    if declared_bytes > MAX_FILTER_BYTES:
        fail("SCREEN_INDEX_BUDGET_EXCEEDED", 413)
    fingerprint = sha(encoded({key: value for key, value in q.items()
                               if key not in ("cursor", "limit")}))
    offset = 0
    if q.get("cursor"):
        cursor = cursor_decode(q["cursor"], env.ZION_PRIVATE_API_TOKEN)
        if cursor.get("query") != fingerprint or cursor.get("index") != m["index_id"]:
            fail("CURSOR_QUERY_OR_RELEASE_MISMATCH", 409)
        offset = cursor.get("offset")
        if type(offset) is not int or not 0 <= offset <= m["issuers"]:
            fail("INVALID_CURSOR_OFFSET", 422)
    read_bytes, found, page, seen = 0, 0, [], {}
    for ref in refs:
        items = json.loads(await object_at(env, ref, MAX_FILTER_BYTES))
        read_bytes += ref["bytes"]
        if len(items) != ref["issuers"]:
            fail("SCREEN_SOURCE_COUNT_MISMATCH", 409)
        in_artifact = set()
        for item in items:
            cik = item.get("cik")
            if not isinstance(cik, str) or not re.fullmatch(r"\d{10}", cik) or cik in in_artifact:
                fail("SCREEN_SOURCE_DUPLICATE_OR_INVALID_CIK", 409)
            in_artifact.add(cik)
            if cik in seen:
                if seen[cik] != item:
                    fail("SCREEN_SOURCE_DUPLICATE_OR_INVALID_CIK", 409)
                continue
            seen[cik] = item
            if ciks and cik not in ciks:
                continue
            if not matches(item, method, flags, missing, status, sectors, share_absence):
                continue
            if offset <= found < offset + limit:
                page.append(item)
            found += 1
    if ciks and set(ciks) - set(seen):
        fail("SCREEN_CIK_NOT_MATERIALIZED", 404)
    if offset > found:
        fail("INVALID_CURSOR_OFFSET", 422)
    end = min(found, offset + limit)
    result = dict(
        base,
        view="screen",
        filters={"method": method, "flag": flags, "missing": missing, "status": status,
                 "sector": sectors, "share_absence": share_absence, "ciks": ciks},
        sector_evidence_states=["REPORTED", *IDENTIFIER_ABSENT_GROUPS],
        matches=page,
        total_matches=found,
        universe=m["issuers"],
        screening="ELIGIBILITY_FROM_THE_PINNED_CLASSIFICATION_RUN",
        ordering="PUBLISHED_BUCKET_ORDER;CIK_UNIQUE_WITHIN_A_RESPONSE",
        next_cursor=cursor_encode(
            {"query": fingerprint, "index": m["index_id"], "offset": end},
            env.ZION_PRIVATE_API_TOKEN,
        )
        if end < found
        else None,
        provenance={"index_manifest": PREFIX + m["index_id"] + "/manifest.json",
                    "index_manifest_sha256": m["_manifest_sha256"],
                    "index_artifacts": [
                        {"key": ref["key"], "sha256": ref["sha256"], "bytes": ref["bytes"]}
                        for ref in refs
                    ],
                    "record_manifest": (m.get("record_manifest") or {}).get("key"),
                    "record_release_id": m["record_release_id"],
                    "licensed_sources_used": False},
        limits={"filter_artifact_bytes": read_bytes, "max_filter_artifact_bytes": MAX_FILTER_BYTES,
                "max_page_matches": MAX_MATCHES, "archive_bytes_read": 0},
    )
    if len(encoded(result)) > MAX_RESPONSE:
        fail("SCREEN_RESPONSE_BUDGET_EXCEEDED", 413)
    return result, 200


async def handle(env, request):
    try:
        return await asyncio.wait_for(execute(env, request), timeout=20)
    except PrivateFinancialsError as error:
        return {"error": {"code": error.code}}, error.status
    except (ValueError, TypeError):
        return {"error": {"code": "INVALID_SCREEN_PARAMETER_OR_CURSOR"}}, 422
    except TimeoutError:
        return {"error": {"code": "READINESS_QUERY_TIMEOUT"}}, 504
    except Exception:
        return {"error": {"code": "READINESS_UNAVAILABLE"}}, 503


def path_matches(url):
    return urlparse(url).path in ("/private/v1/readiness", "/private/v1/readiness/screen")
