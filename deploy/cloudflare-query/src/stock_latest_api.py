"""Expose the existing verified daily publication alongside CRSP historical prices."""

import base64
import json
import re
from urllib.parse import parse_qs, urlparse
from contract_v2 import ContractError
from market_api import market_dispatch
from private_http import authenticate
from private_financials import PrivateFinancialsError
from stock_archive import digest, encode


def fail(code, status=400):
    raise PrivateFinancialsError(code, code, status)


async def execute(env, request):
    authenticate(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    q = parse_qs(urlparse(request.url).query, keep_blank_values=True)
    if set(q) - {
        "source",
        "symbol",
        "search",
        "date_from",
        "date_to",
        "limit",
        "cursor",
        "expected_release_id",
        "as_of",
    } or any(len(v) != 1 for v in q.values()):
        fail("INVALID_PARAMETER")
    p = {k: v[0] for k, v in q.items()}
    if "as_of" in p:
        fail("STOCK_HISTORICAL_AVAILABILITY_NOT_ESTABLISHED", 422)
    symbol = p.get("symbol") or p.get("search", "")
    if symbol and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,19}", symbol):
        fail("INVALID_SYMBOL")
    if not re.fullmatch(r"\d{1,3}", p.get("limit", "100")):
        fail("INVALID_LIMIT")
    limit = int(p.get("limit", "100"))
    if not 1 <= limit <= 500:
        fail("INVALID_LIMIT")
    fingerprint = digest(
        encode({k: v for k, v in p.items() if k not in {"cursor", "expected_release_id"}})
    )
    offset = 0
    if p.get("cursor"):
        try:
            if len(p["cursor"]) > 1500:
                raise ValueError()
            c = json.loads(base64.urlsafe_b64decode(p["cursor"]))
            if c["query"] != fingerprint or c["release"] != p.get("expected_release_id"):
                fail("STOCK_CURSOR_CHANGED", 409)
            offset = c["offset"]
            if type(offset) is not int or offset < 0:
                raise ValueError()
        except PrivateFinancialsError:
            raise
        except Exception:
            fail("INVALID_CURSOR")
    params = dict(q=symbol.upper(), offset=offset, limit=limit)
    for field, target in [
        ("date_from", "start_date"),
        ("date_to", "end_date"),
        ("expected_release_id", "release_id"),
    ]:
        if field in p:
            params[target] = p[field]
    try:
        value = await market_dispatch(env, "/v1/market/prices", params, "owner-stock-latest")
    except ContractError as e:
        fail(e.code, e.status)
    release = value["release"]
    rid = release["market_release_id"]
    rows = value["data"]["prices"]
    page = value["page"]
    result = dict(
        schema_version="zion-stock-history-v1",
        source="ALPACA_" + release["feed"].upper() + "_DAILY",
        release_id=rid,
        coverage=dict(
            value["coverage"],
            time_scope="LATEST_VERIFIED_DAILY_PUBLICATION",
            session=release["session"],
            price_basis="EXPLICIT_PER_SOURCE_ROW",
            search_mode="EXACT_SOURCE_SYMBOL",
            historical_availability="NOT_ESTABLISHED",
        ),
        symbol_count=value["coverage"].get("counts", {}).get("published", page["total"]),
        total_matches=page["total"],
        next_cursor=base64.urlsafe_b64encode(
            encode(dict(offset=page["next_offset"], release=rid, query=fingerprint))
        ).decode()
        if page["next_offset"] is not None
        else None,
        source_pages={rid: release},
        gaps=[
            dict(symbol=s, reason="NO_PRICE_IN_LATEST_PUBLICATION")
            for s in value["coverage"].get("missing_symbols", [])
        ],
    )
    if p.get("symbol"):
        result.update(symbol=symbol.upper(), prices=rows)
    else:
        result["symbols"] = [
            dict(
                symbol=r["symbol"],
                security_id=r.get("security_id"),
                date_min=release["session"],
                date_max=release["session"],
                normalized_rows=1,
            )
            for r in rows
        ]
    return result
