"""Unified, provenance-segmented prices over existing immutable serving sources."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

from contract_v2 import ContractError
from data_api import date_range
from serving_v2 import ServingV2Error, _as_of, _available, artifact, load_release
from company_api import _archive_artifact, _market_release, _market_symbol


MAX_BULK_SYMBOLS = 100
MAX_BULK_ROWS = 100_000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _valid_symbol(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,19}", value))


def _segment_from_rows(rows: list[dict[str, Any]], *, source: str, release_id: str, adjustment_status: str, pit_status: str, currency: str = "USD") -> dict[str, Any]:
    dates = [row.get("session_date", "") for row in rows]
    return {
        "source": source,
        "release_id": release_id,
        "start_date": min(dates) if dates else None,
        "end_date": max(dates) if dates else None,
        "row_count": len(rows),
        "adjustment_status": adjustment_status,
        "pit_status": pit_status,
        "currency": currency,
    }


async def _canonical_rows(env: Any, manifest: dict[str, Any], symbol: str, *, start: str | None, end: str | None, cutoff: Any, resolver: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if resolver is None:
        try:
            resolver = await artifact(env, manifest, "identity/resolver_index.json")
        except ServingV2Error as error:
            if error.code == "SERVING_ARTIFACT_NOT_FOUND":
                return [], None
            raise
    entry = resolver.get("symbols", {}).get(symbol)
    if entry is None or (entry.get("valid_to") is not None and cutoff is None):
        return [], entry
    if cutoff is not None:
        from company_api import _identity_symbol
        entry = _identity_symbol(resolver, symbol, cutoff.isoformat().replace("+00:00", "Z"))
        if entry is None:
            return [], None
    prefix = f"{entry['artifact_path']}/prices/daily/"
    years = sorted((item["path"].split("/")[-1].removesuffix(".json") for item in manifest.get("artifacts", []) if item.get("path", "").startswith(prefix)), reverse=True)
    rows: list[dict[str, Any]] = []
    for year in years:
        if end and year > end[:4]:
            continue
        if start and year < start[:4]:
            break
        values = await artifact(env, manifest, f"{prefix}{year}.json")
        rows.extend(
            {**row, "provenance": {"serving_release_id": manifest["serving_release_id"], "source_snapshot_id": manifest.get("source", {}).get("prices", {}).get("snapshot_id"), "artifact": f"{prefix}{year}.json"}}
            for row in values
            if _available(row, cutoff) and (not start or row.get("session_date", "") >= start) and (not end or row.get("session_date", "") <= end)
        )
    rows.sort(key=lambda row: row.get("session_date", ""))
    return rows, entry


async def _archive_rows(env: Any, manifest: dict[str, Any], symbol: str, *, start: str | None, end: str | None, cutoff: Any, index: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if index is None:
        try:
            index = await _archive_artifact(env, manifest, "archive/index.json")
        except ServingV2Error as error:
            if error.code == "SERVING_ARTIFACT_NOT_FOUND":
                return [], None
            raise
    entry = next((row for row in index if row.get("symbol") == symbol), None)
    if entry is None:
        return [], None
    prefix = f"archive/{symbol}/prices/"
    rows: list[dict[str, Any]] = []
    for item in manifest.get("artifacts", []):
        filename = item.get("path", "")
        if not filename.startswith(prefix):
            continue
        if start and item.get("last_date") and item["last_date"] < start:
            continue
        if end and item.get("first_date") and item["first_date"] > end:
            continue
        payload = await _archive_artifact(env, manifest, filename)
        defaults = payload.get("defaults", {}) if isinstance(payload, dict) else {}
        records = payload.get("rows", []) if isinstance(payload, dict) else payload
        for compact in records:
            row = {**defaults, **compact}
            if (start and row.get("session_date", "") < start) or (end and row.get("session_date", "") > end):
                continue
            if _available(row, cutoff):
                rows.append({**row, "provenance": {"archive_release_id": manifest.get("source", {}).get("archive", {}).get("release_id"), "artifact": filename, "storage_key": item.get("storage_key")}})
    rows.sort(key=lambda row: row.get("session_date", ""))
    return rows, entry


async def price_history(
    env: Any,
    symbol: str,
    params: dict[str, Any],
    request_id: str,
    *,
    _release_context: tuple[dict[str, Any], dict[str, Any]] | None = None,
    _market_context: tuple[dict[str, Any] | None, dict[str, Any] | None, str] | None = None,
    _identity_context: tuple[dict[str, Any] | None, list[dict[str, Any]] | None] | None = None,
) -> dict[str, Any]:
    symbol = unquote(symbol).upper().strip()
    if not _valid_symbol(symbol):
        raise ContractError("INVALID_ARGUMENT", "a valid security symbol is required")
    allowed = {"start_date", "end_date", "as_of", "release_id", "offset", "limit"}
    if not isinstance(params, dict) or set(params) - allowed or any(not isinstance(value, str) and not (key in {"offset", "limit"} and isinstance(value, int) and not isinstance(value, bool)) for key, value in params.items()):
        raise ContractError("INVALID_ARGUMENT", "unsupported or invalid price query parameters")
    start, end = date_range(params)
    try:
        cutoff = _as_of(params.get("as_of"))
    except ServingV2Error as error:
        if error.code == "INVALID_REQUEST":
            raise ContractError("INVALID_ARGUMENT", error.message, status=400) from error
        raise
    try:
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 1000))
    except (TypeError, ValueError):
        raise ContractError("INVALID_ARGUMENT", "offset and limit must be integers")
    if offset < 0 or not 1 <= limit <= 10000:
        raise ContractError("INVALID_ARGUMENT", "offset must be nonnegative and limit must be between 1 and 10000")
    if (start and len(start) != 10) or (end and len(end) != 10):
        raise ContractError("INVALID_ARGUMENT", "price dates must use YYYY-MM-DD")
    if start and end and start > end:
        raise ContractError("INVALID_ARGUMENT", "start_date must precede end_date")
    pointer = manifest = None
    if _release_context is not None:
        pointer, manifest = _release_context
    else:
        try:
            pointer, manifest = await load_release(env)
        except ServingV2Error as error:
            if error.code == "INVALID_REQUEST":
                raise ContractError("INVALID_ARGUMENT", error.message, status=400) from error
            if error.code != "SERVING_ARTIFACT_NOT_FOUND":
                raise
    serving_id = manifest["serving_release_id"] if manifest else None
    if params.get("release_id") and params["release_id"] != serving_id:
        raise ContractError("RELEASE_CHANGED", "restart the price query against the current release", status=409)
    resolver_index, archive_index = _identity_context or (None, None)
    if manifest is not None:
        canonical, identity = await _canonical_rows(env, manifest, symbol, start=start, end=end, cutoff=cutoff, resolver=resolver_index)
        archive, archive_identity = await _archive_rows(env, manifest, symbol, start=start, end=end, cutoff=cutoff, index=archive_index)
    else:
        canonical, identity, archive, archive_identity = [], None, [], None
    segments: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    if canonical:
        segments.append(_segment_from_rows(canonical, source="canonical_daily_prices", release_id=serving_id, adjustment_status="SOURCE_DECLARED_PER_ROW", pit_status="PIT_ELIGIBLE_PER_ROW"))
        series.extend(canonical)
    if archive:
        archive_release = manifest.get("source", {}).get("archive", {}).get("release_id", "unknown") if manifest else "unknown"
        segments.append(_segment_from_rows(archive, source="source_archive", release_id=archive_release, adjustment_status="SOURCE_PRESERVED_RETROSPECTIVE_ADJUSTMENT_UNKNOWN", pit_status="SOURCE_LIMITED_NOT_PIT_CERTIFIED"))
        series.extend(archive)
    current_release = None
    market_rows: list[dict[str, Any]] = []
    latest_market_session = manifest.get("source", {}).get("prices", {}).get("latest_date", "0000-00-00") if manifest else "0000-00-00"
    should_check_market = cutoff is None and (manifest is None or end is None or end > latest_market_session)
    current_status = "NOT_USED_FOR_PIT" if cutoff is not None else "NOT_IN_RANGE" if not should_check_market else "DATA_NOT_PUBLISHED"
    if should_check_market:
        if _market_context is None:
            try:
                current_context, current_data = await _market_release(env, request_id)
                market_status = "AVAILABLE"
            except ContractError as error:
                if error.code != "SOURCE_LIMITED":
                    raise
                current_context, current_data, market_status = None, None, "SOURCE_LIMITED"
        else:
            current_context, current_data, market_status = _market_context
        if current_context is not None and current_data is not None:
            current_session = current_context["release"].get("session")
            in_range = (not start or not current_session or current_session >= start) and (not end or not current_session or current_session <= end)
            if in_range:
                current_release = current_context["release"]["market_release_id"]
                market_rows, _ = _market_symbol(current_data, symbol)
                if market_rows:
                    market_rows = [{**row, "available_at": None, "source_id": "Alpaca " + current_data["feed"] + " daily bars", "unit": "USD/share", "currency": "USD", "provenance": {"market_release_id": current_release, "data_key": current_context["release"]["data_key"], "sha256": current_release}} for row in market_rows]
                    segments.append(_segment_from_rows(market_rows, source="qualified_current_market", release_id=current_release, adjustment_status="raw", pit_status="NOT_ESTABLISHED"))
                    series.extend(market_rows)
                    current_status = "AVAILABLE"
                else:
                    current_status = "SOURCE_LIMITED"
            else:
                current_status = "NOT_IN_RANGE"
        else:
            current_status = market_status

    market_identity = None
    if current_release is not None and current_data is not None and market_rows:
        market_identity = {
            "symbol": symbol,
            "provider_symbol": symbol,
            "security_id": market_rows[0].get("security_id"),
            "exchange": market_rows[0].get("exchange"),
            "instrument_id": market_rows[0].get("instrument_id"),
            "listing_id": market_rows[0].get("listing_id"),
            "source": "daily_market_release",
        }
    has_identity = identity is not None or archive_identity is not None or market_identity is not None
    if not canonical and not archive and current_release is None and not has_identity:
        raise ContractError("SECURITY_NOT_FOUND", f"{symbol} is not in this release", status=404)
    if not canonical and not archive and current_release is not None and not series:
        raise ContractError("SECURITY_NOT_FOUND", f"{symbol} has no qualified price data in this release", status=404)
    series.sort(key=lambda row: (row.get("session_date", ""), row.get("provenance", {}).get("market_release_id", "")))
    total = len(series)
    selected = series[offset:offset + limit]
    if len(json.dumps({"series": selected, "segments": segments}, separators=(",", ":")).encode()) > MAX_RESPONSE_BYTES:
        raise ContractError("RESPONSE_TOO_LARGE", "price response exceeds the 8 MiB limit; request fewer rows", status=413)
    return {
        "symbol": symbol,
        "as_of": params.get("as_of"),
        "release": {"serving_release_id": serving_id, "market_release_id": current_release} if serving_id is not None else {"market_release_id": current_release} if current_release else {},
        "identity": {"canonical": identity, "archive": archive_identity, "market": market_identity},
        "series": selected,
        "segments": segments,
        "page": {"offset": offset, "limit": limit, "total": total, "next_offset": offset + limit if offset + limit < total else None, "serving_release_id": serving_id, "market_release_id": current_release},
        "availability": {"canonical": "AVAILABLE" if canonical else "DATA_NOT_PUBLISHED", "archive": "AVAILABLE" if archive else "SOURCE_LIMITED" if archive_identity else "DATA_NOT_PUBLISHED", "current_market": current_status},
        "releases": {"canonical": serving_id if canonical else None, "archive": manifest.get("source", {}).get("archive", {}).get("release_id") if archive_identity and manifest else None, "market": current_release},
        "request_id": request_id,
    }


async def bulk_price_history(env: Any, body: Any, request_id: str) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"symbols", "start_date", "end_date", "frequency", "as_of"}:
        raise ContractError("INVALID_ARGUMENT", "bulk prices requires symbols and an optional date range/frequency/as_of")
    raw_symbols = body.get("symbols")
    if not isinstance(raw_symbols, list) or not 1 <= len(raw_symbols) <= MAX_BULK_SYMBOLS or any(not isinstance(value, str) for value in raw_symbols):
        raise ContractError("INVALID_ARGUMENT", f"request 1-{MAX_BULK_SYMBOLS} unique valid symbols")
    symbols = [value.strip().upper() for value in raw_symbols]
    if any(not _valid_symbol(value) for value in symbols) or len(set(symbols)) != len(symbols):
        raise ContractError("INVALID_ARGUMENT", f"request 1-{MAX_BULK_SYMBOLS} unique valid symbols")
    if body.get("frequency", "daily") != "daily":
        raise ContractError("INVALID_ARGUMENT", "only daily frequency is published")
    params = {key: str(body[key]) for key in ("start_date", "end_date", "as_of") if body.get(key) is not None}
    date_range(params)
    release_context = await load_release(env)
    _, manifest = release_context
    if body.get("as_of") is not None:
        try:
            cutoff = _as_of(str(body["as_of"]))
        except ServingV2Error as error:
            if error.code == "INVALID_REQUEST":
                raise ContractError("INVALID_ARGUMENT", error.message, status=400) from error
            raise
    else:
        cutoff = None
    try:
        resolver_index = await artifact(env, manifest, "identity/resolver_index.json")
    except ServingV2Error as error:
        if error.code != "SERVING_ARTIFACT_NOT_FOUND":
            raise
        resolver_index = None
    try:
        archive_index = await _archive_artifact(env, manifest, "archive/index.json")
    except ServingV2Error as error:
        if error.code != "SERVING_ARTIFACT_NOT_FOUND":
            raise
        archive_index = None
    identity_context = (resolver_index, archive_index)
    latest_canonical = manifest.get("source", {}).get("prices", {}).get("latest_date", "0000-00-00")
    check_market = params.get("as_of") is None and (not params.get("end_date") or params["end_date"] > latest_canonical)
    market_context = None
    if check_market:
        try:
            market_context = (*await _market_release(env, request_id), "AVAILABLE")
        except ContractError as error:
            if error.code != "SOURCE_LIMITED":
                raise
            market_context = (None, None, "SOURCE_LIMITED")
    result = {"release": {"serving_release_id": manifest["serving_release_id"]}, "data": {"prices": {}}, "coverage": {"status": "PARTIAL", "row_count": 0, "missing_symbols": [], "errors": {}}, "request_id": request_id}
    for symbol in symbols:
        try:
            packet = await price_history(env, symbol, {**params, "release_id": manifest["serving_release_id"], "limit": 10000}, request_id, _release_context=release_context, _market_context=market_context, _identity_context=identity_context)
        except ContractError as error:
            if error.code != "SECURITY_NOT_FOUND":
                raise
            result["coverage"]["missing_symbols"].append(symbol)
            result["coverage"]["errors"][symbol] = {"code": error.code, "message": error.message}
            continue
        result["data"]["prices"][symbol] = packet
        result["coverage"]["row_count"] += len(packet["series"])
        if packet["page"]["next_offset"] is not None:
            result["coverage"]["truncated_symbols"] = sorted(set(result["coverage"].get("truncated_symbols", [])) | {symbol})
        if result["coverage"]["row_count"] > MAX_BULK_ROWS:
            raise ContractError("RESPONSE_TOO_LARGE", "bulk price row limit exceeded", status=413)
        if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_RESPONSE_BYTES:
            raise ContractError("RESPONSE_TOO_LARGE", "bulk price response exceeds the 8 MiB limit; request fewer symbols or a shorter range", status=413)
    result["coverage"]["status"] = "AVAILABLE" if not result["coverage"]["missing_symbols"] else "PARTIAL"
    return result
