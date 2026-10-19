"""Owner-only full stored Alpaca stock-history access, independent of fundamentals coverage."""

import base64
import json
import re
from datetime import date
from urllib.parse import parse_qs, urlparse

from private_financials import PrivateFinancialsError
from private_http import authenticate
from stock_archive import PREFIX, RIGHTS, SCHEMA, digest, encode, parse_page

MAX_READ = 32 * 1048576


def fail(code, status=400):
    raise PrivateFinancialsError(code, code, status)


async def read(env, key, sha, size=None, maximum=1048576):
    obj = await env.MARKET_DATA.get(key)
    if not obj or obj.size > maximum:
        fail("STOCK_SOURCE_UNAVAILABLE", 503)
    raw = (await obj.text()).encode()
    if len(raw) > maximum or digest(raw) != sha or (size is not None and len(raw) != size):
        fail("STOCK_SOURCE_INTEGRITY", 503)
    return raw


async def execute_alpaca(env, request):
    authenticate(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    q = parse_qs(urlparse(request.url).query, keep_blank_values=True)
    allowed = {
        "source",
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
    if p.get("source", "alpaca") != "alpaca":
        fail("INVALID_STOCK_SOURCE")
    if "as_of" in p:
        fail("STOCK_HISTORICAL_AVAILABILITY_NOT_ESTABLISHED", 422)
    symbol = p.get("symbol", "").upper()
    if symbol and not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,24}", symbol):
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
    key = getattr(env, "STOCK_HISTORY_KEY", "")
    sha = getattr(env, "STOCK_HISTORY_SHA256", "")
    if not re.fullmatch(PREFIX + r"[a-f0-9]{64}/manifest.json", key):
        fail("STOCK_INDEX_UNAVAILABLE", 503)
    root = json.loads(await read(env, key, sha))
    if root.get("schema_version") != SCHEMA or root.get("rights") != RIGHTS:
        fail("STOCK_POLICY", 503)
    release = root["release_id"]
    prefix = PREFIX + release + "/"
    if key != prefix + "manifest.json":
        fail("STOCK_INDEX_IDENTITY", 503)
    if p.get("expected_release_id", release) != release:
        fail("STOCK_RELEASE_CHANGED", 409)
    fingerprint = digest(
        encode({k: v for k, v in p.items() if k not in ("cursor", "expected_release_id")})
    )
    offset = 0
    if p.get("cursor"):
        try:
            if len(p["cursor"]) > 1000:
                raise ValueError()
            c = json.loads(base64.urlsafe_b64decode(p["cursor"]))
            if (
                p.get("expected_release_id") != release
                or c["release"] != release
                or c["query"] != fingerprint
            ):
                fail("STOCK_CURSOR_CHANGED", 409)
            offset = c["offset"]
            if type(offset) is not int or not 0 <= offset <= 10000000:
                raise ValueError()
        except PrivateFinancialsError:
            raise
        except Exception:
            fail("INVALID_CURSOR")
    result = dict(
        schema_version=SCHEMA,
        source="ALPACA_SIP",
        release_id=release,
        rights=RIGHTS,
        symbol_count=root["symbol_count"],
        coverage=dict(
            stored_normalized_rows=root["normalized_rows"],
            historical_identity="NOT_ESTABLISHED",
            historical_availability="NOT_ESTABLISHED",
            price_basis="RAW",
            adjustment_method="NONE",
            source_scope="ALL_INDEXED_STORED_ALPACA_SIP;NOT_THE_WHOLE_MARKET_OR_CONTINUOUS_HISTORY",
        ),
    )

    async def shard(suffix):
        ref = root["shards"][suffix]
        if ref["key"] != prefix + "shards/" + suffix + ".json":
            fail("STOCK_SHARD_REFERENCE", 503)
        value = json.loads(await read(env, ref["key"], ref["sha256"], ref["bytes"]))
        return value["symbols"]

    if symbol:
        suffix = digest(symbol.encode())[:2]
        entries = await shard(suffix) if suffix in root["shards"] else {}
        if symbol not in entries:
            fail("STOCK_SYMBOL_NOT_STORED", 404)
        entry = entries[symbol]
        grouped = {}
        gaps = []
        sources = {}
        read_bytes = 0
        for page_id in entry["source_pages"]:
            ref = root["source_pages"][page_id]
            if (
                ref["sha256"] != page_id
                or not ref["key"].startswith("raw/alpaca/equity-bars/")
                or ".." in ref["key"]
                or ref["feed"] != "SIP"
                or ref["price_basis"] != "RAW"
                or ref["adjustment_method"] != "NONE"
            ):
                fail("STOCK_SOURCE_POLICY", 503)
            read_bytes += ref["bytes"]
            if read_bytes > MAX_READ:
                fail("STOCK_SOURCE_READ_BUDGET", 413)
            raw = await read(env, ref["key"], ref["sha256"], ref["bytes"], maximum=8 * 1048576)
            rows, rejected = parse_page(raw, symbol)
            for gap in rejected:
                if p.get("date_from", "0000") <= gap["session_date"] <= p.get("date_to", "9999"):
                    sid = digest(
                        encode(dict(page=page_id, symbol=symbol, session=gap["session_date"]))
                    )
                    gaps.append(dict(gap, source_ids=[sid]))
                    sources[sid] = dict(
                        source_page_sha256=page_id,
                        source_key=ref["key"],
                        session_date=gap["session_date"],
                        provider_symbol=symbol,
                    )
            for row in rows:
                if (
                    not p.get("date_from", "0000")
                    <= row["session_date"]
                    <= p.get("date_to", "9999")
                ):
                    continue
                sid = digest(encode(dict(page=page_id, symbol=symbol, session=row["session_date"])))
                row.update(
                    symbol=symbol,
                    security_id="alpaca:sip:source-symbol:" + symbol,
                    currency=ref["currency"],
                    available_at=None,
                    price_basis="RAW",
                    adjustment_method="NONE",
                )
                identity = digest(encode(row))
                day = row["session_date"]
                variants = grouped.setdefault(day, {})
                variants.setdefault(identity, dict(row, source_ids=[]))["source_ids"].append(sid)
                sources[sid] = dict(
                    source_page_sha256=page_id,
                    source_key=ref["key"],
                    session_date=day,
                    provider_symbol=symbol,
                )
        prices = []
        for day, variants in sorted(grouped.items()):
            if len(variants) > 1:
                gaps.append(
                    dict(
                        session_date=day,
                        reason="CONFLICTING_SOURCE_BARS",
                        source_ids=sorted(
                            {sid for variant in variants.values() for sid in variant["source_ids"]}
                        ),
                    )
                )
            else:
                prices.append(next(iter(variants.values())))
        selected = prices
        result.update(
            symbol=symbol,
            prices=prices[offset : offset + limit],
            gaps=gaps,
            source_read_bytes=read_bytes,
            source_lineage={
                sid: sources[sid]
                for row in prices[offset : offset + limit] + gaps
                for sid in row["source_ids"]
            },
            source_pages={page: root["source_pages"][page] for page in entry["source_pages"]},
        )
    else:
        # Catalog pages use the index's precomputed ordered summary, not all raw price files.
        selected = [
            row
            for row in root.get("catalog", [])
            if not p.get("search") or p["search"].upper() in row["symbol"]
        ]
        if not selected and root["symbol_count"] and not root.get("catalog"):
            for suffix in sorted(root["shards"]):
                selected.extend(dict(symbol=k, **v) for k, v in (await shard(suffix)).items())
            selected.sort(key=lambda r: r["symbol"])
        result["symbols"] = selected[offset : offset + limit]
    result["total_matches"] = len(selected)
    result["next_cursor"] = (
        base64.urlsafe_b64encode(
            encode(dict(release=release, query=fingerprint, offset=offset + limit))
        ).decode()
        if offset + limit < len(selected)
        else None
    )
    return result


async def execute(env, request):
    q = parse_qs(urlparse(request.url).query, keep_blank_values=True)
    source = q.get("source", ["crsp" if getattr(env, "CRSP_HISTORY_KEY", None) else "alpaca"])
    if len(source) != 1 or source[0] not in {"crsp", "alpaca", "alpaca_latest"}:
        fail("INVALID_STOCK_SOURCE")
    if source[0] == "alpaca_latest":
        from stock_latest_api import execute as latest_execute
        return await latest_execute(env, request)
    if source[0] == "crsp":
        from crsp_stock_history_api import execute as crsp_execute
        return await crsp_execute(env, request)
    return await execute_alpaca(env, request)


async def handle(env, request):
    try:
        return await execute(env, request), 200
    except PrivateFinancialsError as error:
        return dict(error=dict(code=error.code)), error.status
    except Exception:
        return dict(error=dict(code="STOCK_HISTORY_UNAVAILABLE")), 503
