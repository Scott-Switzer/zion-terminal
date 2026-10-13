"""Authenticated caller's bounded SEC archive queries and persistent R2 results."""

import hashlib
import json
import re
import struct
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from private_financials import PrivateFinancialsError
from sec_expanded import (
    MAX_COMPRESSED,
    MAX_MEMBER,
    METRICS,
    VERSION,
    decode_member,
    normalize_companyfacts,
)
from temporal_core import parse_instant

_METADATA_CACHE = {}
PREFIX = "gold/sec-expanded/releases/"
ARCHIVE_KEY = "sec/bulk/sec-nightly-20260924/companyfacts.zip"


def fail(code, message, status=422):
    raise PrivateFinancialsError(code, message, status)


def js_options(value):
    try:
        from js import Object
        from pyodide.ffi import to_js

        return to_js(value, dict_converter=Object.fromEntries)
    except ImportError:
        return value


async def metadata(env, reference, maximum=2 * 1048576):
    key, digest = reference["key"], reference["sha256"]
    if (
        not isinstance(key, str)
        or not key.startswith(PREFIX)
        or any(part in ("", ".", "..") for part in key.split("/"))
        or not re.fullmatch(r"[0-9a-f]{64}", str(digest))
    ):
        fail("EXPANSION_INTEGRITY_FAILURE", "invalid source index reference", 503)
    cache_key = (key, digest)
    if cache_key in _METADATA_CACHE:
        return _METADATA_CACHE[cache_key]
    obj = await env.MARKET_DATA.get(key)
    if obj is None or obj.size > maximum:
        fail("EXPANSION_INTEGRITY_FAILURE", "source index is unavailable or oversized", 503)
    raw = (await obj.text()).encode()
    if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != digest:
        fail("EXPANSION_INTEGRITY_FAILURE", "source index digest mismatch", 503)
    value = json.loads(raw)
    if len(_METADATA_CACHE) >= 16:
        _METADATA_CACHE.pop(next(iter(_METADATA_CACHE)))
    _METADATA_CACHE[cache_key] = value
    return value


async def source_member(env, source, entry, cik):
    if (
        source.get("key") != ARCHIVE_KEY
        or source.get("bucket") != "financial-data-wrds-private"
        or entry.get("name") != f"CIK{cik}.json"
        or entry.get("flags", 0) & 1
    ):
        fail("EXPANSION_INTEGRITY_FAILURE", "source member is outside approved scope", 503)
    if entry["uncompressed_bytes"] > MAX_MEMBER or entry["compressed_bytes"] > MAX_COMPRESSED:
        fail("SOURCE_MEMBER_TOO_LARGE", "issuer exceeds bounded serving limits")
    head = await env.SEC_ARCHIVE.head(ARCHIVE_KEY)
    if head is None or head.size != source["bytes"] or head.etag.strip('"') != source["etag"]:
        fail("SOURCE_VERSION_CHANGED", "stored source no longer matches the indexed version", 503)

    async def read(offset, length):
        if length < 1 or length > 1048576 or offset < 0 or offset + length > source["bytes"]:
            fail("EXPANSION_INTEGRITY_FAILURE", "invalid bounded source range", 503)
        obj = await env.SEC_ARCHIVE.get(
            ARCHIVE_KEY,
            js_options(
                {
                    "range": {"offset": offset, "length": length},
                    "onlyIf": {"etagMatches": source["etag"]},
                }
            ),
        )
        if obj is None or obj.etag.strip('"') != source["etag"]:
            fail("SOURCE_VERSION_CHANGED", "source version changed during read", 503)
        value = await obj.arrayBuffer()
        raw = bytes(value.to_py()) if hasattr(value, "to_py") else bytes(value)
        if len(raw) != length:
            fail("EXPANSION_INTEGRITY_FAILURE", "source range length mismatch", 503)
        return raw

    header = struct.unpack("<4s5H3I2H", await read(entry["offset"], 30))
    if header[0] != b"PK\x03\x04" or header[2] & 1 or header[3] != entry["compression"]:
        fail("EXPANSION_INTEGRITY_FAILURE", "source ZIP header mismatch", 503)
    name = await read(entry["offset"] + 30, header[-2])
    if name != entry["name"].encode():
        fail("EXPANSION_INTEGRITY_FAILURE", "source ZIP member identity mismatch", 503)
    offset = entry["offset"] + 30 + header[-2] + header[-1]
    compressed, remaining = [], entry["compressed_bytes"]
    while remaining:
        n = min(remaining, 1048576)
        compressed.append(await read(offset, n))
        offset += n
        remaining -= n
    return decode_member(b"".join(compressed), entry)


async def query_expanded(env, request: dict, *, request_id: str) -> dict:
    try:
        return await _query(env, request, request_id)
    except PrivateFinancialsError:
        raise
    except Exception:
        fail("EXPANDED_DATA_UNAVAILABLE", "expanded source data is unavailable", 503)


async def _query(env, request, request_id):
    operation = request.get("operation")
    allowed = (
        {"operation"}
        if operation == "sec_coverage"
        else {
            "operation",
            "symbol",
            "cik",
            "metrics",
            "period",
            "lookback",
            "as_of",
            "expected_release_id",
        }
    )
    if operation not in ("sec_coverage", "sec_financials") or set(request) - allowed:
        fail("INVALID_REQUEST", "unsupported expanded query arguments", 400)
    key, digest = (
        getattr(env, "SEC_EXPANDED_INDEX_KEY", None),
        getattr(env, "SEC_EXPANDED_INDEX_SHA256", None),
    )
    if not key or not digest:
        fail("EXPANSION_NOT_CONFIGURED", "expanded source index is not configured", 503)
    index = await metadata(env, {"key": key, "sha256": digest}, maximum=65536)
    if index.get("version") != "zion-sec-expanded-index-v1":
        fail("EXPANSION_INTEGRITY_FAILURE", "source index schema mismatch", 503)
    release = {
        "serving_release_id": "sec-expanded:" + index["release_id"],
        "sec_archive_release_id": index["release_id"],
        "index_sha256": digest,
        "snapshot_date": index["source"]["snapshot_date"],
        "normalization_version": VERSION,
    }
    if operation == "sec_coverage":
        return {
            "schema_version": "zion-private-query-v1",
            "request_id": request_id,
            "operation": operation,
            "data": {
                **index["counts"],
                "metrics": list(METRICS),
                "identity_policy": index["identity_policy"],
                "financial_validation": "NOT_INDEPENDENTLY_VALIDATED",
            },
            "release": release,
        }
    if bool(request.get("symbol")) == bool(request.get("cik")):
        fail("INVALID_REQUEST", "specify exactly one symbol or CIK", 400)
    metrics = request.get("metrics", ["revenue"])
    period, lookback = request.get("period", "annual"), request.get("lookback", 10)
    if (
        not isinstance(metrics, list)
        or not 1 <= len(metrics) <= 20
        or any(not isinstance(m, str) or m not in METRICS for m in metrics)
        or len(set(metrics)) != len(metrics)
        or period not in ("annual", "quarterly")
        or type(lookback) is not int
        or not 1 <= lookback <= 40
    ):
        fail("INVALID_REQUEST", "invalid metrics, period or lookback", 400)
    as_of = request.get("as_of")
    if as_of is not None:
        try:
            instant = parse_instant(as_of)
            if instant.as_datetime > datetime.now(timezone.utc):
                raise ValueError()
        except (ValueError, TypeError):
            fail("INVALID_REQUEST", "as_of must be a nonfuture timezone-aware instant", 400)
        if request.get("symbol"):
            fail(
                "DATED_IDENTITY_NOT_ESTABLISHED",
                "use CIK for historical requests; ticker mapping is a stored snapshot",
            )
    else:
        as_of = (
            date.fromisoformat(index["source"]["snapshot_date"]) + timedelta(days=2)
        ).isoformat() + "T00:00:00Z"
    symbol = request.get("symbol")
    if symbol:
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,19}", symbol):
            fail("INVALID_REQUEST", "invalid company symbol", 400)
        cik = (await metadata(env, index["tickers"])).get(symbol.upper())
        if not cik:
            fail("ENTITY_NOT_FOUND", "symbol is not linked in the stored SEC snapshot", 404)
    else:
        cik = request["cik"]
        if not isinstance(cik, str) or not re.fullmatch(r"\d{1,10}", cik):
            fail("INVALID_REQUEST", "CIK must contain at most ten digits", 400)
        cik = cik.zfill(10)
    if (
        request.get("expected_release_id", release["serving_release_id"])
        != release["serving_release_id"]
    ):
        fail("RELEASE_MISMATCH", "expanded source release differs from expected release", 409)
    query_key = {
        "cik": cik,
        "metrics": sorted(metrics),
        "period": period,
        "lookback": lookback,
        "as_of": as_of,
        "normalization": VERSION,
    }
    cache_digest = hashlib.sha256(json.dumps(query_key, sort_keys=True).encode()).hexdigest()
    cache_key = f"gold/sec-expanded/results/{index['release_id']}/{cik}/{cache_digest}.json"
    cached = await env.MARKET_DATA.get(cache_key)
    if cached is not None:
        if cached.size > 1048576:
            fail("EXPANSION_INTEGRITY_FAILURE", "cached query exceeds budget", 503)
        raw = (await cached.text()).encode()
        actual = hashlib.sha256(raw).hexdigest()
        custom = cached.customMetadata
        recorded = (
            custom.get("sha256") if isinstance(custom, dict) else getattr(custom, "sha256", None)
        )
        if len(raw) > 1048576 or actual != recorded:
            fail("EXPANSION_INTEGRITY_FAILURE", "cached query digest mismatch", 503)
        result = json.loads(raw)
        if result.get("release") != release:
            fail("EXPANSION_INTEGRITY_FAILURE", "cached release mismatch", 503)
        return {
            **result,
            "entity": {**result["entity"], "requested_symbol": symbol.upper() if symbol else None},
            "request_id": request_id,
            "cache": "HIT",
        }
    shard = index["shards"].get(cik[-2:])
    entry = (await metadata(env, shard)).get(cik) if shard else None
    if entry is None:
        fail("ENTITY_NOT_FOUND", "issuer is absent or exceeds the bounded source budget", 404)
    raw = await source_member(env, index["source"], entry, cik)
    company = json.loads(raw, parse_float=Decimal)
    data = normalize_companyfacts(
        company, cik=cik, metrics=metrics, period=period, lookback=lookback, as_of=as_of
    )
    entity = data.pop("entity")
    entity["requested_symbol"] = symbol.upper() if symbol else None
    result = {
        "schema_version": "zion-private-query-v1",
        "operation": operation,
        "entity": entity,
        "data": data,
        "release": release,
        "coverage": {
            "status": "PARTIAL" if data["missing_metrics"] or data["conflicts"] else "AVAILABLE",
            "as_of": as_of,
            "source_snapshot_date": index["source"]["snapshot_date"],
            "identity_policy": index["identity_policy"],
            "live_provider_calls": 0,
            "archive_digest_status": index["archive_sha256_status"],
            "member_sha256": hashlib.sha256(raw).hexdigest(),
            "missing_metrics": data["missing_metrics"],
            "independent_validation": "NOT_ESTABLISHED",
        },
    }
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > 1048576:
        fail("QUERY_RESULT_TOO_LARGE", "reduce metrics or lookback", 413)
    await env.MARKET_DATA.put(
        cache_key,
        encoded.decode(),
        js_options(
            {
                "httpMetadata": {"contentType": "application/json"},
                "customMetadata": {"sha256": hashlib.sha256(encoded).hexdigest()},
                "onlyIf": {"etagDoesNotMatch": "*"},
            }
        ),
    )
    return {**result, "request_id": request_id, "cache": "MISS"}
