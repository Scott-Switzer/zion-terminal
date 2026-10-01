"""Zion-owned company packet composition over canonical and current market releases."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import unquote

from contract_v2 import ContractError
from data_api import date_range
from market_api import market_dispatch
from serving_v2 import (
    ServingV2Error, _as_of, _available, _serve_row, _select_revisions,
    artifact, load_release,
)


def _identity_symbol(resolver_index: dict[str, Any], symbol: str, as_of: Any = None) -> dict[str, Any] | None:
    entry = resolver_index.get("symbols", {}).get(symbol)
    if entry is None:
        return None
    if as_of is None:
        return entry if entry.get("valid_to") is None else None
    from temporal_core import normalize_source_instant
    point = normalize_source_instant(as_of, field="as_of").epoch_ns
    start, end = entry.get("valid_from"), entry.get("valid_to")
    if start and normalize_source_instant(start, field="valid_from").epoch_ns > point:
        return None
    if end and normalize_source_instant(end, field="valid_to").epoch_ns < point:
        return None
    return entry



def _missing_artifact(error: ServingV2Error) -> bool:
    return error.code == "SERVING_ARTIFACT_NOT_FOUND"


async def _archive_artifact(env: Any, manifest: dict[str, Any], relative: str) -> Any:
    entry = next((item for item in manifest.get("artifacts", []) if item.get("path") == relative), None)
    if entry is None:
        raise ServingV2Error("SERVING_ARTIFACT_NOT_FOUND", f"serving artifact is not in the manifest: {relative}")
    storage_key = entry.get("storage_key")
    if not isinstance(storage_key, str) or not storage_key.startswith("gold/serving/releases/") or not storage_key.endswith(".json"):
        raise ServingV2Error("SERVING_MANIFEST_CORRUPT", "archive artifact reference is invalid")
    obj = await env.MARKET_DATA.get(storage_key)
    if obj is None:
        raise ServingV2Error("SERVING_ARTIFACT_NOT_FOUND", f"archive artifact is unavailable: {relative}")
    raw = (await obj.text()).encode()
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        raise ServingV2Error("SERVING_ARTIFACT_CORRUPT", f"archive artifact hash mismatch: {relative}")
    try:
        return json.loads(raw)
    except Exception as exc:
        raise ServingV2Error("SERVING_ARTIFACT_CORRUPT", f"archive artifact JSON is invalid: {relative}") from exc


async def company_dispatch(env: Any, path: str, params: dict[str, Any], request_id: str) -> dict[str, Any]:
    if not isinstance(params, dict) or set(params) - {"as_of", "start_date", "end_date", "limit"}:
        raise ContractError("INVALID_ARGUMENT", "unsupported company query parameters")
    if any(not isinstance(value, str) and not (key == "limit" and isinstance(value, int) and not isinstance(value, bool)) for key, value in params.items()):
        raise ContractError("INVALID_ARGUMENT", "company query parameters must be strings")
    symbol = unquote(path.removeprefix("/v1/company/")).upper().strip()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,19}", symbol):
        raise ContractError("INVALID_ARGUMENT", "a valid security symbol is required")
    start, end = date_range(params)
    cutoff_value = params.get("as_of")
    if cutoff_value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff_value):
        # Match the REST API's date-only contract: an end-of-day UTC cutoff,
        # not midnight at the start of the requested calendar date.
        cutoff_value = f"{cutoff_value}T23:59:59.999999Z"
    cutoff = _as_of(cutoff_value)
    try:
        limit = int(params.get("limit", 500))
    except (ValueError, TypeError):
        raise ContractError("INVALID_ARGUMENT", "limit must be an integer")
    if not 1 <= limit <= 1000:
        raise ContractError("INVALID_ARGUMENT", "limit must be between 1 and 1000")

    canonical_release = None
    canonical_manifest = None
    archive_release = None
    archive_entry: dict[str, Any] | None = None
    archive_rows: list[dict[str, Any]] = []
    identity: dict[str, Any] = {"symbol": symbol, "canonical": None, "market": None, "archive": None}
    annual: list[dict[str, Any]] = []
    quarterly: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    fundamentals_status = "DATA_NOT_PUBLISHED"
    history_status = "DATA_NOT_PUBLISHED"
    snapshot = None

    try:
        _, canonical_manifest = await load_release(env)
        canonical_release = canonical_manifest["serving_release_id"]
    except ServingV2Error as error:
        if not _missing_artifact(error):
            raise

    if canonical_manifest is not None:
        archive_source = canonical_manifest.get("source", {}).get("archive", {})
        archive_release = archive_source.get("release_id")
        if archive_release:
            try:
                archive_index = await _archive_artifact(env, canonical_manifest, "archive/index.json")

                archive_entry = next((row for row in archive_index if row.get("symbol") == symbol), None)
                if archive_entry is not None:
                    identity["archive"] = {"symbol": symbol, "source_security": archive_entry}
                    prefix_archive = f"archive/{symbol}/prices/"
                    archive_items = sorted((item for item in canonical_manifest.get("artifacts", []) if item.get("path", "").startswith(prefix_archive)), key=lambda item: item.get("path", ""))
                    for item in archive_items:
                        filename = item["path"]
                        first_date, last_date = item.get("first_date"), item.get("last_date")
                        if start and last_date and last_date < start:
                            continue
                        if end and first_date and first_date > end:
                            continue
                        payload = await _archive_artifact(env, canonical_manifest, filename)
                        defaults = payload.get("defaults", {}) if isinstance(payload, dict) else {}
                        records = payload.get("rows", []) if isinstance(payload, dict) else payload
                        for compact in records:
                            row = {**defaults, **compact}
                            if (start and row.get("session_date", "") < start) or (end and row.get("session_date", "") > end):
                                continue
                            if _available(row, cutoff):
                                archive_rows.append({**row, "provenance": {"archive_release_id": archive_release, "serving_release_id": canonical_release, "artifact": filename, "storage_key": item.get("storage_key")}})
                    archive_rows.sort(key=lambda row: row.get("session_date", ""))
                    archive_rows = archive_rows[-limit:]
            except ServingV2Error as error:
                if not _missing_artifact(error):
                    raise

        resolver_index = None
        try:
            resolver_index = await artifact(env, canonical_manifest, "identity/resolver_index.json")
        except ServingV2Error as error:
            if not _missing_artifact(error):
                raise
        canonical_identity = _identity_symbol(resolver_index or {}, symbol, cutoff_value)
        if canonical_identity is not None:
            identity["canonical"] = canonical_identity
            artifact_path = canonical_identity["artifact_path"]
            try:
                snapshot = await artifact(env, canonical_manifest, f"{artifact_path}/snapshot.json")
            except ServingV2Error as error:
                if not _missing_artifact(error):
                    raise
        if snapshot is not None and canonical_identity is not None:
            identity["canonical"] = {**canonical_identity, "entity_id": snapshot.get("entity_id"), "instrument_ids": snapshot.get("instrument_ids", [])}
            for period, target in (("annual", annual), ("quarterly", quarterly)):
                artifact_path_period = f"{artifact_path}/fundamentals/{period}.json"
                try:
                    rows = await artifact(env, canonical_manifest, artifact_path_period)
                except ServingV2Error as error:
                    if not _missing_artifact(error):
                        raise
                    continue
                rows = [
                    {
                        **row,
                        "metric_id": row.get("metric_id", row.get("metric")),
                        "period_end": row.get("period_end", row.get("period")),
                        "value_decimal": row.get("value_decimal", row.get("value")),
                        "source_id": row.get("source_id", row.get("source")),
                        "source_record_id": row.get("source_record_id", row.get("source_record")),
                    }
                    for row in rows
                ]
                rows = [row for row in rows if _available(row, cutoff) and (not start or row.get("period_end", "") >= start) and (not end or row.get("period_end", "") <= end)]
                revisions.extend({**row, **_serve_row(row, release_id=canonical_release, artifact_path=artifact_path_period, source_snapshot_id=canonical_manifest.get("source", {}).get("fundamentals", {}).get("snapshot_id"))} for row in rows)
                rows = _select_revisions(rows, cutoff)
                target.extend({**row, **_serve_row(row, release_id=canonical_release, artifact_path=artifact_path_period, source_snapshot_id=canonical_manifest.get("source", {}).get("fundamentals", {}).get("snapshot_id"))} for row in rows)
        if snapshot is not None and canonical_identity is not None:
            fundamentals_status = "AVAILABLE" if annual or quarterly else "DATA_NOT_PUBLISHED"
            annual.sort(key=lambda row: (row.get("period_end", ""), row.get("metric_id", "")))
            quarterly.sort(key=lambda row: (row.get("period_end", ""), row.get("metric_id", "")))
            annual = annual[-1000:]
            quarterly = quarterly[-1000:]
            revisions.sort(key=lambda row: (row.get("period_end", ""), row.get("available_at", ""), row.get("observation_id", "")))
            revisions = revisions[-1000:]

            prefix = f"{artifact_path}/prices/daily/"
            years = sorted({item["path"].split("/")[-1].removesuffix(".json") for item in canonical_manifest.get("artifacts", []) if item.get("path", "").startswith(prefix)}, reverse=True)
            for year in years:
                if end and year > end[:4]:
                    continue
                if start and year < start[:4]:
                    break
                try:
                    rows = await artifact(env, canonical_manifest, f"{prefix}{year}.json")
                except ServingV2Error as error:
                    if not _missing_artifact(error):
                        raise
                    continue
                history.extend({**row, "provenance": {"serving_release_id": canonical_release, "source_snapshot_id": canonical_manifest.get("source", {}).get("prices", {}).get("snapshot_id"), "artifact": f"{prefix}{year}.json"}} for row in rows if _available(row, cutoff) and (not start or row.get("session_date", "") >= start) and (not end or row.get("session_date", "") <= end))
                if not start and len(history) >= limit:
                    break
            history.sort(key=lambda row: row.get("session_date", ""))
            history = history[-limit:]
            history_status = "AVAILABLE" if history else "DATA_NOT_PUBLISHED"

    latest_market = None
    market_release = None
    market_status = "NOT_USED_FOR_PIT" if cutoff is not None else "DATA_NOT_PUBLISHED"
    market_published_at = None
    if cutoff is None:
        try:
            market_result = await market_dispatch(env, f"/v1/market/prices/{symbol}", {}, request_id)
        except ContractError as error:
            if error.code != "SOURCE_LIMITED":
                raise
            market_result = None
            market_status = "SOURCE_LIMITED"
        if market_result is not None:
            market_release = market_result.get("release", {}).get("market_release_id")
            market_published_at = market_result.get("release", {}).get("published_at")
            prices = market_result.get("data", {}).get("prices", [])
            if prices:
                latest_market = prices[0]
                identity["market"] = {key: latest_market.get(key) for key in ("security_id", "instrument_id", "listing_id", "symbol", "provider_symbol", "exchange") if latest_market.get(key) is not None}
                market_status = "AVAILABLE"
            else:
                market_status = "SOURCE_LIMITED" if symbol in market_result.get("coverage", {}).get("missing_symbols", []) else "DATA_NOT_PUBLISHED"

    # A current identity/snapshot is not historical financial evidence. A PIT
    # request must contain at least one eligible canonical observation.
    supported = bool(history or annual or quarterly or archive_rows) if cutoff is not None else bool(snapshot is not None or history or annual or quarterly or archive_rows or latest_market)
    if not supported:
        if cutoff is not None:
            raise ContractError("HISTORICAL_DATA_UNAVAILABLE", "no canonical historical security data is published for this request; the current market release is not eligible", status=422)
        raise ContractError("SECURITY_NOT_FOUND", "security has no published canonical or qualified market data", status=404)
    if cutoff is not None and not history and not annual and not quarterly:
        fundamentals_status = "DATA_NOT_PUBLISHED"
    elif annual or quarterly:
        fundamentals_status = "AVAILABLE"
    latest_observation = latest_market
    latest_release = market_release if latest_market else None
    if cutoff is not None and history:
        latest_observation = history[-1]
        latest_release = None
        market_status = "NOT_USED_FOR_PIT"
    elif cutoff is not None:
        market_status = "NOT_USED_FOR_PIT"

    return {
        "symbol": symbol,
        "as_of": params.get("as_of"),
        "status": "AVAILABLE" if fundamentals_status == "AVAILABLE" and (history_status == "AVAILABLE" if cutoff is not None else market_status == "AVAILABLE" or history_status == "AVAILABLE") else "PARTIAL",
        "identity": identity,
        "releases": {"fundamentals": canonical_release if annual or quarterly else None, "revisions": canonical_release if revisions else None, "canonical_prices": canonical_release if history else None, "archive": archive_release if archive_entry is not None else None, "market_price": market_release if latest_market else None},
        "fundamentals": {"status": fundamentals_status, "annual": annual, "quarterly": quarterly},
        "revisions": {"status": "AVAILABLE" if revisions else "DATA_NOT_PUBLISHED", "observations": revisions},
        "prices": {
            "latest": ({"status": "AVAILABLE", "observation": latest_observation, "freshness": {"session": latest_observation.get("session_date"), "published_at": market_published_at if latest_market else None, "provider_timestamp": latest_observation.get("provider_timestamp", latest_observation.get("timestamp")), "retrieved_at": latest_observation.get("retrieved_at"), "ingested_at": latest_observation.get("ingested_at"), "available_at": latest_observation.get("available_at")}} if latest_observation else {"status": market_status, "observation": None, "freshness": None}),
            "history": {"status": history_status, "observations": history},
            "archive": {"status": "AVAILABLE" if archive_rows else "SOURCE_LIMITED" if archive_entry is not None else "DATA_NOT_PUBLISHED", "pit_status": "SOURCE_LIMITED", "adjustment_status": archive_entry.get("adjustment_status", "source_preserved_retrospective_adjustment_unknown") if archive_entry is not None else None, "observations": archive_rows},
        },
        "request_id": request_id,
    }
