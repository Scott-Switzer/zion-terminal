"""Feature-flagged adapter for PPE Financial Serving V2 releases.

This module deliberately has no provider or legacy-release fallback. When enabled,
missing, corrupt, or incompatible serving data is an explicit error.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

CURRENT_KEY = "gold/serving/CURRENT.json"
SCHEMA_VERSION = "financial-serving-v2"


class ServingV2Error(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def enabled(env: Any) -> bool:
    return str(getattr(env, "SERVING_V2_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}


def entity_key(symbol: str) -> str:
    return f"real_equity_{symbol.upper()}"


def cache_key(release_id: str, artifact_path: str) -> str:
    # Release identity is part of the key. A promotion can never return an
    # artifact from another immutable release through a warm cache entry.
    return f"https://serving-v2.internal/{release_id}/{artifact_path.lstrip('/')}"


def _as_of(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ServingV2Error("INVALID_REQUEST", "as_of must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ServingV2Error("INVALID_REQUEST", "as_of must be a valid ISO-8601 timestamp") from exc


def _available(row: dict[str, Any], as_of: datetime | None) -> bool:
    if as_of is None:
        return True
    value = row.get("available_at")
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=as_of.tzinfo)
        return parsed <= as_of
    except ValueError:
        return False


def _value(row: dict[str, Any]) -> Any:
    if row.get("value_decimal") not in (None, ""):
        return row["value_decimal"]
    value = row.get("value")
    return str(value) if isinstance(value, Decimal) else value


def _serve_row(row: dict[str, Any], *, release_id: str, artifact_path: str, source_snapshot_id: int | None = None) -> dict[str, Any]:
    return {
        "entity_id": row.get("entity_id"),
        "instrument_id": row.get("instrument_id"),
        "metric": row.get("metric_id"),
        "value": _value(row),
        "unit": row.get("unit"),
        "period": row.get("period_end"),
        "period_type": row.get("period_type"),
        "period_start": row.get("period_start"),
        "available_at": row.get("available_at"),
        "ingested_at": row.get("ingested_at"),
        "source_record": row.get("source_record_id"),
        "source_revision_id": row.get("source_revision_id"),
        "form": row.get("form"),
        "evidence_id": row.get("evidence_id"),
        "derivation_type": row.get("derivation_type"),
        "calculation_id": row.get("calculation_id"),
        "producer_release": row.get("producer_release"),
        "provenance": {"serving_release_id": release_id, "artifact": artifact_path, "source_snapshot_id": source_snapshot_id},
    }


def _select_revisions(rows: list[dict[str, Any]], as_of: datetime | None) -> list[dict[str, Any]]:
    eligible = [row for row in rows if _available(row, as_of)]
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in eligible:
        identity = (row.get("metric_id"), row.get("period_type"), row.get("period_start"), row.get("period_end"))
        prior = selected.get(identity)
        if prior is None or (row.get("available_at", ""), row.get("form", "")) > (prior.get("available_at", ""), prior.get("form", "")):
            selected[identity] = row
    return sorted(selected.values(), key=lambda row: (row.get("period_end") or "", row.get("metric_id") or ""))


async def _r2_text(env: Any, key: str) -> bytes:
    obj = await env.MARKET_DATA.get(key)
    if obj is None:
        raise ServingV2Error("SERVING_ARTIFACT_NOT_FOUND", f"serving artifact is unavailable: {key}", retryable=True)
    return (await obj.text()).encode()


async def _cached_text(env: Any, key: str, *, release_id: str | None, ttl: int) -> bytes:
    # Cloudflare's Cache API is optional in local/unit runtimes. Any cache API
    # failure falls through to the authoritative R2 object, never to another
    # data source. Cache keys remain release-addressed.
    if release_id:
        try:
            from js import Request, caches  # type: ignore
            request = Request.new(cache_key(release_id, key))
            hit = await caches.default.match(request)
            if hit is not None:
                return (await hit.text()).encode()
        except Exception:
            pass
    raw = await _r2_text(env, key)
    if release_id:
        try:
            from js import Request, Response, caches  # type: ignore
            request = Request.new(cache_key(release_id, key))
            # The release-addressed URL is the cache identity. Response
            # construction stays string-based for Pyodide compatibility.
            await caches.default.put(request, Response.new(raw.decode()))
        except Exception:
            pass
    return raw


async def load_release(env: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    # CURRENT is intentionally fetched uncached. Its tiny mutable pointer must
    # refresh immediately after promotion/rollback; immutable artifacts carry
    # the long-lived release-addressed cache identity.
    current_raw = await _r2_text(env, getattr(env, "SERVING_V2_CURRENT_KEY", CURRENT_KEY))
    try:
        current = json.loads(current_raw)
    except Exception as exc:
        raise ServingV2Error("SERVING_POINTER_INVALID", "serving CURRENT is invalid") from exc
    release_id = current.get("serving_release_id")
    if not isinstance(release_id, str) or not release_id:
        raise ServingV2Error("SERVING_POINTER_INVALID", "serving CURRENT has no release identity")
    manifest_key = current.get("manifest_key")
    if not isinstance(manifest_key, str) or not manifest_key.startswith("gold/serving/releases/"):
        raise ServingV2Error("SERVING_POINTER_INVALID", "serving CURRENT has an unsafe manifest key")
    manifest_raw = await _cached_text(env, manifest_key, release_id=release_id, ttl=31536000)
    expected = current.get("manifest_sha256")
    if expected and hashlib.sha256(manifest_raw).hexdigest() != expected:
        raise ServingV2Error("SERVING_MANIFEST_CORRUPT", "serving manifest hash mismatch")
    try:
        manifest = json.loads(manifest_raw)
    except Exception as exc:
        raise ServingV2Error("SERVING_MANIFEST_CORRUPT", "serving manifest is invalid") from exc
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("serving_release_id") != release_id:
        raise ServingV2Error("SERVING_SCHEMA_MISMATCH", "serving manifest identity or schema mismatch")
    return current, manifest


async def artifact(env: Any, manifest: dict[str, Any], relative: str) -> Any:
    release_id = manifest["serving_release_id"]
    prefix = f"gold/serving/releases/{release_id}/"
    entry = next((item for item in manifest.get("artifacts", []) if item.get("path") == relative), None)
    if entry is None:
        raise ServingV2Error("SERVING_ARTIFACT_NOT_FOUND", f"artifact is not in the manifest: {relative}")
    raw = await _cached_text(env, prefix + relative, release_id=release_id, ttl=31536000)
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        raise ServingV2Error("SERVING_ARTIFACT_CORRUPT", f"artifact hash mismatch: {relative}")
    try:
        return json.loads(raw)
    except Exception as exc:
        raise ServingV2Error("SERVING_ARTIFACT_CORRUPT", f"artifact JSON is invalid: {relative}") from exc


async def fundamentals(env: Any, symbol: str, metrics: list[str], *, period: str | None = None, lookback: int = 40, as_of: Any = None, request_id: str = "") -> dict[str, Any]:
    _, manifest = await load_release(env)
    key = entity_key(symbol)
    snapshot = await artifact(env, manifest, f"entities/{key}/snapshot.json")
    source = {"annual": "annual", "quarterly": "quarterly"}.get(period or "quarterly", "quarterly")
    rows = await artifact(env, manifest, f"entities/{key}/fundamentals/{source}.json")
    selected = _select_revisions([row for row in rows if not metrics or row.get("metric_id") in metrics], _as_of(as_of))
    selected = [row for row in selected if not metrics or row.get("metric_id") in metrics]
    if lookback > 0:
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for row in selected:
            by_metric.setdefault(row.get("metric_id", ""), []).append(row)
        selected = [row for metric_rows in by_metric.values() for row in metric_rows[-min(lookback, 40):]]
        selected.sort(key=lambda row: (row.get("period_end") or "", row.get("metric_id") or ""))
    if metrics and {row.get("metric_id") for row in selected} < set(metrics):
        raise ServingV2Error("METRIC_NOT_AVAILABLE", "one or more requested metrics are unavailable")
    served = [_serve_row(row, release_id=manifest["serving_release_id"], artifact_path=f"entities/{key}/fundamentals/{source}.json", source_snapshot_id=manifest["source"]["fundamentals"]["snapshot_id"]) for row in selected]
    return {"world": {"world_type": "real", "world_id": "us-public-markets", "version": manifest["serving_release_id"]}, "entity": {"entity_id": snapshot["entity_id"], "symbol": snapshot["symbol"], "display_name": snapshot["symbol"]}, "observations": served, "release": {"serving_release_id": manifest["serving_release_id"], "source": manifest["source"]}, "request_id": request_id}


async def price_history(env: Any, symbol: str, *, limit: int = 500, start_date: str | None = None, end_date: str | None = None, as_of: Any = None, request_id: str = "") -> dict[str, Any]:
    _, manifest = await load_release(env)
    key = entity_key(symbol)
    snapshot = await artifact(env, manifest, f"entities/{key}/snapshot.json")
    prefix = f"entities/{key}/prices/daily/"
    years = sorted({item["path"].split("/")[-1].removesuffix(".json") for item in manifest.get("artifacts", []) if item.get("path", "").startswith(prefix)})
    rows: list[dict[str, Any]] = []
    for year in reversed(years):
        if end_date and year > end_date[:4]:
            continue
        if start_date and year < start_date[:4]:
            break
        try:
            rows.extend(await artifact(env, manifest, f"entities/{key}/prices/daily/{year}.json"))
        except ServingV2Error as exc:
            if exc.code != "SERVING_ARTIFACT_NOT_FOUND":
                raise
        if not start_date and not end_date and len(rows) >= limit:
            break
    rows = [row for row in rows if _available(row, _as_of(as_of)) and (not start_date or row.get("session_date", "") >= start_date) and (not end_date or row.get("session_date", "") <= end_date)]
    rows = sorted(rows, key=lambda row: row["session_date"])[-min(limit, 500):]
    for row in rows:
        row["provenance"] = {"serving_release_id": manifest["serving_release_id"], "source_snapshot_id": manifest["source"]["prices"]["snapshot_id"], "artifact": f"entities/{key}/prices/daily/{row['session_date'][:4]}.json"}
    return {"world": {"world_type": "real", "world_id": "us-public-markets", "version": manifest["serving_release_id"]}, "prices": rows, "release": {"serving_release_id": manifest["serving_release_id"], "source": manifest["source"]}, "request_id": request_id}


async def query(env: Any, symbol: str, metrics: list[str], *, as_of: Any = None, request_id: str = "") -> dict[str, Any]:
    fundamental_metrics = [metric for metric in metrics if metric != "last_price"]
    observations = []
    release = None
    entity = None
    if fundamental_metrics:
        result = await fundamentals(env, symbol, fundamental_metrics, period="annual", lookback=1, as_of=as_of, request_id=request_id)
        observations.extend(result["observations"])
        release = result["release"]
        entity = result["entity"]
    if "last_price" in metrics:
        result = await latest_price(env, symbol, as_of=as_of, request_id=request_id)
        observations.append(result["metric"])
        release = release or result["release"]
        entity = entity or {"entity_id": f"real:equity:{symbol}", "symbol": symbol, "display_name": symbol}
    if not observations:
        raise ServingV2Error("METRIC_NOT_AVAILABLE", "requested metrics are unavailable")
    return {"world": {"world_type": "real", "world_id": "us-public-markets", "version": release["serving_release_id"]}, "entity": entity, "observations": observations, "release": release, "request_id": request_id}


async def latest_price(env: Any, symbol: str, *, as_of: Any = None, request_id: str = "") -> dict[str, Any]:
    _, manifest = await load_release(env)
    key = entity_key(symbol)
    snapshot = await artifact(env, manifest, f"entities/{key}/snapshot.json")
    row = snapshot.get("latest_price")
    if not row or not _available(row, _as_of(as_of)):
        history = await price_history(env, symbol, limit=1, as_of=as_of, request_id=request_id)
        if not history["prices"]:
            raise ServingV2Error("METRIC_NOT_AVAILABLE", "price is unavailable")
        row = history["prices"][-1]
    return {"world": {"world_type": "real", "world_id": "us-public-markets", "version": manifest["serving_release_id"]}, "metric": {"metric": "last_price", "value": row["close"], "unit": row["unit"], "period": row["session_date"], "available_at": row["available_at"], "volume": row.get("volume"), "evidence_id": row.get("evidence_id"), "provenance": {"serving_release_id": manifest["serving_release_id"], "source_snapshot_id": manifest["source"]["prices"]["snapshot_id"], "artifact": f"entities/{key}/prices/daily/{row['session_date'][:4]}.json"}}, "release": {"serving_release_id": manifest["serving_release_id"], "source": manifest["source"]}, "request_id": request_id}
