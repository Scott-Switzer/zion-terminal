"""Serve stored CRSP by stable security ID through the existing private datasets service."""

import base64
import json
import re
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlparse

from crsp_archive import PREFIX, RIGHTS, SCHEMA, digest, encode, native_point
from private_financials import PrivateFinancialsError
from private_http import authenticate

_MANIFEST_CACHE = None
MAX_NATIVE_REQUESTS = 16
COLUMNS = (
    "permno,dlycaldt,dlyprc,dlyprcflg,dlyret,dlyretx,dlyretmissflg,dlydistretflg,dlydelflg,dlyvol"
)


def fail(code, status=400):
    raise PrivateFinancialsError(code, code, status)


async def read(env, key, sha, *, size=None, maximum=16 * 1048576):
    global _MANIFEST_CACHE
    is_manifest = key.endswith("/manifest.json")
    if is_manifest and _MANIFEST_CACHE is not None and _MANIFEST_CACHE[:2] == (key, sha):
        return _MANIFEST_CACHE[2]
    obj = await env.SEC_ARCHIVE.get(key)
    if not obj or obj.size > maximum:
        fail("CRSP_INDEX_UNAVAILABLE", 503)
    raw = (await obj.text()).encode()
    if len(raw) > maximum or digest(raw) != sha or (size is not None and len(raw) != size):
        fail("CRSP_INDEX_INTEGRITY", 503)
    value = json.loads(raw)
    if is_manifest:
        _MANIFEST_CACHE = (key, sha, value)
    return value


async def fetch_rows(env, source, offset, count):
    from workers import Request

    if (
        not source["key"].startswith("raw/wrds/")
        or ".." in source["key"]
        or not source["key"].endswith(".parquet")
        or not re.fullmatch(r"[A-Za-z0-9._-]+", source["etag"])
        or not 1 <= count <= 1000
        or offset < 0
        or offset + count > source["rows"]
    ):
        fail("CRSP_SOURCE_REFERENCE", 503)
    binding = getattr(env, "PRIVATE_DATASETS", None)
    if binding is None:
        fail("CRSP_NATIVE_SERVICE_UNAVAILABLE", 503)
    target = "https://zion-datasets.internal/v1/datasets/research/rows?" + urlencode(
        dict(path=source["key"], columns=COLUMNS, offset=offset, limit=count)
    )
    req = Request(
        target, method="GET", headers={"if-match": '"' + source["etag"] + '"'}, redirect="manual"
    )
    response = await binding.fetch(req.js_object)
    raw = (await response.text()).encode()
    if response.status != 200:
        fail(
            "CRSP_SOURCE_VERSION_CHANGED"
            if response.status == 412
            else "CRSP_NATIVE_SOURCE_UNAVAILABLE",
            503,
        )
    if len(raw) > 2 * 1048576:
        fail("CRSP_NATIVE_RESPONSE_BUDGET", 503)
    value = json.loads(raw, parse_float=Decimal)
    actual = value.get("source", {})
    rows = value.get("data", {}).get("rows")
    if (
        actual.get("store") != "research"
        or actual.get("path") != source["key"]
        or actual.get("etag") != source["etag"]
        or actual.get("bytes") != source["bytes"]
        or not isinstance(rows, list)
        or len(rows) != count
    ):
        fail("CRSP_NATIVE_SOURCE_INTEGRITY", 503)
    return rows


def aliases(entry):
    return entry.get("aliases", [r["symbol"] for r in entry.get("intervals", [])])


def matches(entry, query):
    q = query.upper()
    return (
        not q
        or q in str(entry["permno"])
        or q in entry.get("name", "").upper()
        or any(q in s.upper() for s in aliases(entry))
    )


async def execute(env, request):
    authenticate(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    q = parse_qs(urlparse(request.url).query, keep_blank_values=True)
    allowed = {
        "source",
        "permno",
        "symbol",
        "search",
        "date_from",
        "date_to",
        "limit",
        "cursor",
        "expected_release_id",
        "as_of",
    }
    if set(q) - allowed or any(len(v) != 1 for v in q.values()):
        fail("INVALID_PARAMETER")
    p = {k: v[0] for k, v in q.items()}
    if p.get("source", "crsp") != "crsp":
        fail("INVALID_STOCK_SOURCE")
    if "as_of" in p:
        fail("STOCK_HISTORICAL_AVAILABILITY_NOT_ESTABLISHED", 422)
    if p.get("permno") and (not re.fullmatch(r"[1-9][0-9]{0,9}", p["permno"]) or p.get("symbol")):
        fail("INVALID_CRSP_SECURITY_ID")
    if p.get("symbol") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,24}", p["symbol"]):
        fail("INVALID_SYMBOL")
    if len(p.get("search", "")) > 64:
        fail("INVALID_SEARCH")
    for field in ("date_from", "date_to"):
        if field in p:
            try:
                if date.fromisoformat(p[field]).isoformat() != p[field]:
                    raise ValueError()
            except ValueError:
                fail("INVALID_DATE")
    if p.get("date_from", "0000") > p.get("date_to", "9999"):
        fail("INVALID_DATE_RANGE")
    if not re.fullmatch(r"\d{1,3}", p.get("limit", "100")):
        fail("INVALID_LIMIT")
    limit = int(p.get("limit", "100"))
    if not 1 <= limit <= 500:
        fail("INVALID_LIMIT")
    key, sha = getattr(env, "CRSP_HISTORY_KEY", ""), getattr(env, "CRSP_HISTORY_SHA256", "")
    if not re.fullmatch(PREFIX + r"[a-f0-9]{64}/manifest.json", key):
        fail("CRSP_INDEX_UNAVAILABLE", 503)
    root = await read(env, key, sha)
    if (
        root.get("schema_version") != SCHEMA
        or root.get("rights") != RIGHTS
        or root.get("source_bucket") != "financial-data-wrds-private"
    ):
        fail("CRSP_INDEX_POLICY", 503)
    release = root["release_id"]
    prefix = PREFIX + release + "/"
    if key != prefix + "manifest.json":
        fail("CRSP_INDEX_IDENTITY", 503)
    if p.get("expected_release_id", release) != release:
        fail("STOCK_RELEASE_CHANGED", 409)
    fingerprint = digest(
        encode({k: v for k, v in p.items() if k not in {"cursor", "expected_release_id", "source"}})
    )
    cursor = {"offset": 0, "fragment": 0, "row": 0}
    if p.get("cursor"):
        try:
            if len(p["cursor"]) > 1500:
                raise ValueError()
            cursor = json.loads(base64.urlsafe_b64decode(p["cursor"]))
            if (
                cursor["release"] != release
                or cursor["query"] != fingerprint
                or p.get("expected_release_id") != release
            ):
                fail("STOCK_CURSOR_CHANGED", 409)
            if any(
                type(cursor.get(k)) is not int or cursor[k] < 0
                for k in ("offset", "fragment", "row")
            ):
                raise ValueError()
        except PrivateFinancialsError:
            raise
        except Exception:
            fail("INVALID_CURSOR")
    catalog = root["catalog"]
    result = dict(
        schema_version="zion-stock-history-v1",
        source="WRDS_CRSP",
        rights=RIGHTS,
        release_id=release,
        security_count=root["security_count"],
        symbol_count=root["security_count"],
        indexed_security_count=root.get("indexed_security_count"),
        priced_security_count=sum(bool(e.get("normalized_rows")) for e in catalog),
        coverage=dict(
            stored_normalized_rows=root["indexed_rows"],
            historical_identity="NATIVE_DATED_PERMNO_NAME_INTERVALS",
            historical_availability="NOT_ESTABLISHED",
            price_basis="NATIVE_CRSP_CIZ_DLYPRC",
            adjustment_method="NOT_DERIVED",
            source_scope="ALL_INDEXED_STORED_CRSP_CIZ_SECURITIES",
            default_history_source="WRDS_CRSP",
            other_sources=["ALPACA_SIP_SOURCE_SYMBOLS"],
        ),
    )
    if p.get("permno") or p.get("symbol"):
        if p.get("permno"):
            selected = next((e for e in catalog if str(e["permno"]) == p["permno"]), None)
        else:
            found = [
                e for e in catalog if any(s.upper() == p["symbol"].upper() for s in aliases(e))
            ]
            if len(found) > 1:
                fail("CRSP_AMBIGUOUS_TICKER", 409)
            selected = found[0] if found else None
        if selected is None:
            fail("CRSP_SECURITY_NOT_FOUND", 404)
        permno = str(selected["permno"])
        suffix = digest(permno.encode())[:2]
        ref = root["shards"][suffix]
        if ref["key"] != prefix + "shards/" + suffix + ".json":
            fail("CRSP_SHARD_REFERENCE", 503)
        shard = await read(env, ref["key"], ref["sha256"], size=ref["bytes"])
        if shard.get("schema_version") != SCHEMA:
            fail("CRSP_SHARD_SCHEMA", 503)
        entry = shard["securities"][permno]
        fragments = [
            r
            for r in entry["fragments"]
            if not (p.get("date_from") and r.get("date_max") and r["date_max"] < p["date_from"])
            and not (p.get("date_to") and r.get("date_min") and r["date_min"] > p["date_to"])
        ]
        fragments.sort(key=lambda r: (r.get("date_min") or "", r["source"], r["offset"]))
        fi, row_at = cursor["fragment"], cursor["row"]
        if fi > len(fragments) or (fi < len(fragments) and row_at > fragments[fi]["rows"]):
            fail("INVALID_CURSOR")
        prices = []
        lineage = {}
        sources = {}
        reads = 0
        while fi < len(fragments) and len(prices) < limit and reads < MAX_NATIVE_REQUESTS:
            fragment = fragments[fi]
            if row_at >= fragment["rows"]:
                fi += 1
                row_at = 0
                continue
            count = min(1000, fragment["rows"] - row_at, limit - len(prices))
            source = root["sources"][fragment["source"]]
            rows = await fetch_rows(env, source, fragment["offset"] + row_at, count)
            reads += 1
            for i, native in enumerate(rows):
                if str(native.get("permno")) != permno:
                    fail("CRSP_SECURITY_SOURCE_MISMATCH", 503)
                point = native_point(native)
                day = point["session_date"]
                if not p.get("date_from", "0000") <= day <= p.get("date_to", "9999"):
                    continue
                identity = [
                    r
                    for r in entry.get("intervals", [])
                    if (not r.get("valid_from") or r["valid_from"] <= day)
                    and (not r.get("valid_to") or day <= r["valid_to"])
                ]
                symbols = sorted({r["symbol"] for r in identity if r.get("symbol")})
                sid = digest(
                    encode(
                        dict(
                            source=fragment["source"],
                            offset=fragment["offset"] + row_at + i,
                            permno=permno,
                        )
                    )
                )
                point.update(
                    security_id="CRSP:PERMNO:" + permno,
                    symbol=symbols[0] if len(symbols) == 1 else None,
                    identity_status="NATIVE_DATED_INTERVAL"
                    if len(symbols) == 1
                    else "NO_UNIQUE_NAME_INTERVAL",
                    close=point["native_price"],
                    open=None,
                    high=None,
                    low=None,
                    currency=None,
                    available_at=None,
                    price_basis="NATIVE_CRSP_CIZ_DLYPRC",
                    source_ids=[sid],
                )
                prices.append(point)
                lineage[sid] = dict(
                    source_key=source["key"],
                    source_etag=source["etag"],
                    source_sha256_declared=source["sha256"],
                    source_offset=fragment["offset"] + row_at + i,
                    security_id=point["security_id"],
                    session_date=day,
                    integrity="ETAG_PINNED_NATIVE_RANGES",
                )
                sources[fragment["source"]] = source
            row_at += count
            if row_at >= fragment["rows"]:
                fi += 1
                row_at = 0
        more = fi < len(fragments)
        result.update(
            permno=int(permno),
            security_id="CRSP:PERMNO:" + permno,
            symbol=selected.get("symbol"),
            prices=prices,
            identity_intervals=entry.get("intervals", []),
            source_lineage=lineage,
            source_pages=sources,
            total_stored_rows=selected.get("normalized_rows"),
            total_matches=None,
            native_source_requests=reads,
            ordering="DATE_FRAGMENT_THEN_NATIVE_SOURCE_ORDER",
            currency_status="NATIVE_CURRENCY_CONTRACT_NOT_ATTACHED",
            gaps=[] if entry["fragments"] else [dict(reason="NO_STORED_CRSP_DAILY_ROWS")],
        )
        cursor.update(fragment=fi, row=row_at)
    else:
        selected = [e for e in catalog if matches(e, p.get("search", ""))]
        result.update(
            symbols=selected[cursor["offset"] : cursor["offset"] + limit],
            total_matches=len(selected),
        )
        cursor["offset"] += limit
        more = cursor["offset"] < len(selected)
    result["next_cursor"] = (
        base64.urlsafe_b64encode(encode(dict(cursor, release=release, query=fingerprint))).decode()
        if more
        else None
    )
    return result
