"""Feature-flagged adapter for PPE Financial Serving V2 releases.

This module deliberately has no provider or legacy-release fallback. When enabled,
missing, corrupt, or incompatible serving data is an explicit error.
"""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter
from decimal import Decimal
from typing import Any

from temporal_core import TEMPORAL_CONTRACT_SHA256, TEMPORAL_SCHEMA_VERSION, TemporalError, normalize_source_instant, parse_instant

CURRENT_KEY = "gold/serving/CURRENT.json"
SCHEMA_VERSION = "financial-serving-v2"
_TELEMETRY: ContextVar[dict[str, Any] | None] = ContextVar("serving_v2_telemetry", default=None)
# These are intentionally initialized lazily on the first request. Initializing
# them at module import could make a deploy-time memory snapshot look like one
# shared isolate identity across multiple runtime isolates.
_ISOLATE_INSTANCE_ID: str | None = None
_ISOLATE_REQUEST_SEQ = 0
_ISOLATE_FIRST_REQUEST_AT: str | None = None


def begin_telemetry(env: Any = None) -> None:
    global _ISOLATE_INSTANCE_ID, _ISOLATE_REQUEST_SEQ, _ISOLATE_FIRST_REQUEST_AT
    diagnostic = str(getattr(env, "DIAGNOSTIC_ISOLATE_TELEMETRY", "false")).lower() in {"1", "true", "yes", "on"}
    if diagnostic:
        if _ISOLATE_INSTANCE_ID is None:
            import uuid
            _ISOLATE_INSTANCE_ID = uuid.uuid4().hex
            _ISOLATE_FIRST_REQUEST_AT = datetime.now(timezone.utc).isoformat()
        _ISOLATE_REQUEST_SEQ += 1
    stats = {
        "r2_gets": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_errors": 0,
        "current_uncached": True,
        "current_cache_hit": 0,
        "current_cache_miss": 0,
        "current_cache_age_ms": 0.0,
        "current_cache_ttl_ms": 0,
        "current_r2_get_ms": 0.0,
        "current_serving_release_id": None,
        "_started": perf_counter(),
        "_timing_ms": {},
    }
    if diagnostic:
        stats.update({
            "isolate_instance_id": _ISOLATE_INSTANCE_ID,
            "isolate_request_seq": _ISOLATE_REQUEST_SEQ,
            "isolate_first_request_at": _ISOLATE_FIRST_REQUEST_AT,
        })
    _TELEMETRY.set(stats)


def _mark(name: str, elapsed: float) -> None:
    stats = _TELEMETRY.get()
    if stats is not None:
        stats["_timing_ms"][name] = round(stats["_timing_ms"].get(name, 0.0) + elapsed * 1000, 3)


def telemetry_snapshot() -> dict[str, Any]:
    stats = _TELEMETRY.get()
    if not stats:
        return {}
    result = {key: value for key, value in stats.items() if not key.startswith("_")}
    result["timing_ms"] = dict(stats.get("_timing_ms", {}))
    result["current_uncached"] = stats.get("current_cache_ttl_ms", 0) <= 0
    return result


def finish_telemetry() -> dict[str, Any]:
    stats = _TELEMETRY.get()
    if stats:
        _mark("total_worker_path", perf_counter() - stats["_started"])
    return telemetry_snapshot()


class ServingV2Error(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def _ttl_ms(env: Any) -> int:
    """CURRENT pointer cache TTL in milliseconds; 0 (or invalid) keeps reads uncached."""
    raw = getattr(env, "CURRENT_POINTER_CACHE_TTL_MS", 0)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


class CurrentPointerCache:
    """Bounded isolate-local cache for the CURRENT serving pointer.

    - TTL 0 (default) disables the cache entirely; every request reads CURRENT.
    - Entries expire on a hard deadline: age >= TTL forces a synchronous R2
      refresh. No stale-if-error, no last-known-good, no silent extension.
    - Identity is the CURRENT object key, so staging/prod or worlds cannot
      share pointer entries. Each isolate may hold its own pointer, so the
      guarantee is per-isolate staleness <= TTL, not cluster-wide refresh.
    - A single in-flight refresh per isolate collapses concurrent expired
      resolutions; correctness is preserved if it cannot engage.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, ...], dict[str, Any]] = {}
        self._inflight: dict[tuple[str, ...], Any] = {}

    @staticmethod
    def identity(env: Any) -> tuple[str, ...]:
        return (str(getattr(env, "SERVING_V2_CURRENT_KEY", CURRENT_KEY)),)

    def reset_for_tests(self) -> None:
        self._entries.clear()
        self._inflight.clear()

    def _parse(self, raw: bytes) -> dict[str, Any]:
        try:
            pointer = json.loads(raw)
        except Exception as exc:
            raise ServingV2Error("SERVING_POINTER_INVALID", "serving CURRENT is invalid") from exc
        release_id = pointer.get("serving_release_id") if isinstance(pointer, dict) else None
        manifest_key = pointer.get("manifest_key") if isinstance(pointer, dict) else None
        if not isinstance(release_id, str) or not release_id or not isinstance(manifest_key, str) or not manifest_key.startswith("gold/serving/releases/"):
            raise ServingV2Error("SERVING_POINTER_INVALID", "serving CURRENT has no release identity")
        return pointer

    async def _fetch(self, env: Any, now: Any) -> tuple[dict[str, Any], int]:
        fetch_started = perf_counter()
        raw = await _r2_text(env, str(getattr(env, "SERVING_V2_CURRENT_KEY", CURRENT_KEY)))
        elapsed = perf_counter() - fetch_started
        pointer = self._parse(raw)
        _mark("current_r2_get", elapsed)
        return pointer, round(elapsed * 1000, 3)

    async def resolve(self, env: Any, *, now: Any = None) -> dict[str, Any]:
        """Resolve the CURRENT pointer under the configured hard TTL."""
        import asyncio

        stats = _TELEMETRY.get()
        ttl = _ttl_ms(env)
        if stats is not None:
            stats["current_cache_ttl_ms"] = ttl
        clock = now or perf_counter
        identity = self.identity(env)
        if ttl <= 0:
            pointer, elapsed = await self._fetch(env, clock)
            if stats is not None:
                stats["current_cache_miss"] += 1
                stats["current_r2_get_ms"] = elapsed
            return pointer
        entry = self._entries.get(identity)
        age_ms = None if entry is None else (clock() - entry["fetched_at"]) * 1000
        if entry is not None and age_ms < ttl:
            if stats is not None:
                stats["current_cache_hit"] += 1
                stats["current_cache_age_ms"] = round(age_ms, 3)
            return entry["pointer"]
        inflight = self._inflight.get(identity)
        if inflight is not None:
            pointer, elapsed = await asyncio.shield(inflight)
            if stats is not None:
                stats["current_cache_hit"] += 1
                stats["current_serving_release_id"] = pointer.get("serving_release_id")
            return pointer
        async def refresh() -> tuple[dict[str, Any], int]:
            try:
                return await self._fetch(env, clock)
            finally:
                self._inflight.pop(identity, None)
        task = asyncio.ensure_future(refresh())
        self._inflight[identity] = task
        try:
            pointer, elapsed = await task
        except BaseException:
            raise
        if stats is not None:
            stats["current_cache_miss"] += 1
            stats["current_r2_get_ms"] = elapsed
            stats["current_serving_release_id"] = pointer.get("serving_release_id")
        self._entries[identity] = {"pointer": pointer, "fetched_at": clock()}
        return pointer


_POINTER_CACHE = CurrentPointerCache()


def current_pointer_cache() -> CurrentPointerCache:
    return _POINTER_CACHE


def enabled(env: Any) -> bool:
    return str(getattr(env, "SERVING_V2_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}


def entity_key(symbol: str) -> str:
    return f"real_equity_{symbol.upper()}"


def _identity_covers(entry: dict[str, Any], as_of: Any) -> bool:
    if not as_of:
        return entry.get("valid_to") is None
    point = normalize_source_instant(as_of, field="as_of").epoch_ns
    start = entry.get("valid_from")
    end = entry.get("valid_to")
    if start and normalize_source_instant(start, field="valid_from").epoch_ns > point:
        return False
    if end and normalize_source_instant(end, field="valid_to").epoch_ns < point:
        return False
    return True


async def resolve_security(env: Any, manifest: dict[str, Any], symbol: str, as_of: Any = None) -> dict[str, Any]:
    """Resolve a canonical or provider symbol before touching financial artifacts."""
    resolver_entry = None
    try:
        index = await artifact(env, manifest, "identity/resolver_index.json")
        candidates = [index.get("symbols", {}).get(symbol.upper().strip())]
        resolver_entry = next((item for item in candidates if item and _identity_covers(item, as_of)), None)
    except ServingV2Error as error:
        if error.code != "SERVING_ARTIFACT_NOT_FOUND":
            raise
    if resolver_entry is None:
        # Compatibility releases predate identity artifacts. Preserve their
        # immutable path semantics while making the fallback explicit.
        resolver_entry = {"entity": None, "instrument": None, "listing": None, "artifact_path": f"entities/{entity_key(symbol)}", "symbol": symbol.upper()}
    return {"query": {"symbol": symbol, "as_of": as_of}, "identity": resolver_entry, "serving_release_id": manifest["serving_release_id"], "temporal_contract_sha256": TEMPORAL_CONTRACT_SHA256}


def cache_key(release_id: str, artifact_path: str) -> str:
    # Release identity is part of the key. A promotion can never return an
    # artifact from another immutable release through a warm cache entry.
    return f"https://serving-v2.internal/{release_id}/{artifact_path.lstrip('/')}"


def _as_of(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return parse_instant(value, field="as_of").as_datetime
    except TemporalError as exc:
        raise ServingV2Error("INVALID_REQUEST", str(exc)) from exc


def _available(row: dict[str, Any], as_of: datetime | None) -> bool:
    if as_of is None:
        return True
    value = row.get("available_at")
    if not isinstance(value, str):
        return False
    try:
        parsed = normalize_source_instant(value, field="available_at").as_datetime
        return parsed <= as_of
    except TemporalError:
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
    started = perf_counter()
    stats = _TELEMETRY.get()
    if stats is not None:
        stats["r2_gets"] += 1
    obj = await env.MARKET_DATA.get(key)
    if obj is None:
        _mark("r2_get", perf_counter() - started)
        raise ServingV2Error("SERVING_ARTIFACT_NOT_FOUND", f"serving artifact is unavailable: {key}", retryable=True)
    raw = (await obj.text()).encode()
    _mark("r2_get", perf_counter() - started)
    return raw


async def _cached_text(env: Any, key: str, *, release_id: str | None, ttl: int) -> bytes:
    # Cloudflare's Cache API is optional in local/unit runtimes. Any cache API
    # failure falls through to the authoritative R2 object, never to another
    # data source. Cache keys remain release-addressed.
    if release_id:
        cache_started = perf_counter()
        try:
            from js import Request, caches  # type: ignore
            request = Request.new(cache_key(release_id, key))
            hit = await caches.default.match(request)
            if hit is not None:
                stats = _TELEMETRY.get()
                if stats is not None: stats["cache_hits"] += 1
                _mark("cache_lookup", perf_counter() - cache_started)
                return (await hit.text()).encode()
            stats = _TELEMETRY.get()
            if stats is not None: stats["cache_misses"] += 1
            _mark("cache_lookup", perf_counter() - cache_started)
        except Exception:
            stats = _TELEMETRY.get()
            if stats is not None:
                stats["cache_errors"] += 1
                stats["cache_misses"] += 1
            _mark("cache_lookup", perf_counter() - cache_started)
    raw = await _r2_text(env, key)
    if release_id:
        try:
            from js import Request, Response, caches  # type: ignore
            request = Request.new(cache_key(release_id, key))
            # The release-addressed URL is the cache identity. Mutating the
            # Headers object avoids Pyodide's JS constructor dictionary issue.
            response = Response.new(raw.decode())
            response.headers.set("Cache-Control", f"public, max-age={ttl}, immutable")
            await caches.default.put(request, response)
        except Exception:
            stats = _TELEMETRY.get()
            if stats is not None: stats["cache_errors"] += 1
    return raw


async def load_release(env: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    # CURRENT resolution goes through the bounded pointer cache. With the
    # default TTL of 0 this is exactly the previous uncached R2 read. A nonzero
    # TTL bounds pointer staleness per isolate; immutable artifacts always use
    # their release-addressed cache path.
    current = await current_pointer_cache().resolve(env)
    release_id = current.get("serving_release_id")
    if not isinstance(release_id, str) or not release_id:
        raise ServingV2Error("SERVING_POINTER_INVALID", "serving CURRENT has no release identity")
    manifest_key = current.get("manifest_key")
    if not isinstance(manifest_key, str) or not manifest_key.startswith("gold/serving/releases/"):
        raise ServingV2Error("SERVING_POINTER_INVALID", "serving CURRENT has an unsafe manifest key")
    manifest_raw = await _cached_text(env, manifest_key, release_id=release_id, ttl=31536000)
    expected = current.get("manifest_sha256")
    hash_started = perf_counter()
    if expected and hashlib.sha256(manifest_raw).hexdigest() != expected:
        _mark("hash_validation", perf_counter() - hash_started)
        raise ServingV2Error("SERVING_MANIFEST_CORRUPT", "serving manifest hash mismatch")
    _mark("hash_validation", perf_counter() - hash_started)
    parse_started = perf_counter()
    try:
        manifest = json.loads(manifest_raw)
    except Exception as exc:
        _mark("json_parse", perf_counter() - parse_started)
        raise ServingV2Error("SERVING_MANIFEST_CORRUPT", "serving manifest is invalid") from exc
    _mark("json_parse", perf_counter() - parse_started)
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
    hash_started = perf_counter()
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        _mark("hash_validation", perf_counter() - hash_started)
        raise ServingV2Error("SERVING_ARTIFACT_CORRUPT", f"artifact hash mismatch: {relative}")
    _mark("hash_validation", perf_counter() - hash_started)
    parse_started = perf_counter()
    try:
        value = json.loads(raw)
    except Exception as exc:
        _mark("json_parse", perf_counter() - parse_started)
        raise ServingV2Error("SERVING_ARTIFACT_CORRUPT", f"artifact JSON is invalid: {relative}") from exc
    _mark("json_parse", perf_counter() - parse_started)
    return value


async def corporate_actions(env: Any, *, symbol: str | None = None, entity_id: str | None = None, instrument_id: str | None = None, start: str | None = None, end: str | None = None, as_of: Any = None, action_types: list[str] | None = None, request_id: str = "") -> dict[str, Any]:
    _, manifest = await load_release(env)
    rows = await artifact(env, manifest, "corporate-actions/actions.json")
    target = {value for value in (entity_id, instrument_id) if value}
    resolved = None
    if symbol:
        resolved = await resolve_security(env, manifest, symbol, as_of)
        target.update(value for value in (resolved["identity"].get("entity"), resolved["identity"].get("instrument")) if value)
    selected = []
    for row in rows:
        refs = {row.get("entity_id"), row.get("instrument_id"), row.get("listing_id"), row.get("predecessor_entity_id"), row.get("successor_entity_id"), row.get("predecessor_instrument_id"), row.get("successor_instrument_id")}
        if target and not refs.intersection(target):
            continue
        if action_types and row.get("action_type") not in action_types:
            continue
        event_date = row.get("effective_date") or row.get("announcement_at") or ""
        if start and event_date[:10] < start: continue
        if end and event_date[:10] > end: continue
        if as_of and normalize_source_instant(row["available_at"], field="available_at").epoch_ns > normalize_source_instant(as_of, field="as_of").epoch_ns:
            continue
        selected.append(row)
    return {"world": {"world_type": "real", "world_id": "us-public-markets", "version": manifest["serving_release_id"]}, "data": {"actions": sorted(selected, key=lambda row: (row.get("effective_date") or "", row["action_id"]))}, "evidence": selected, "release": {"serving_release_id": manifest["serving_release_id"], "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_hash": TEMPORAL_CONTRACT_SHA256}, "request_id": request_id}


async def fundamentals(env: Any, symbol: str, metrics: list[str], *, period: str | None = None, lookback: int = 40, as_of: Any = None, request_id: str = "") -> dict[str, Any]:
    _, manifest = await load_release(env)
    resolved = await resolve_security(env, manifest, symbol, as_of)
    key = resolved["identity"]["artifact_path"]
    snapshot = await artifact(env, manifest, f"{key}/snapshot.json")
    source = {"annual": "annual", "quarterly": "quarterly"}.get(period or "quarterly", "quarterly")
    rows = await artifact(env, manifest, f"{key}/fundamentals/{source}.json")
    filter_started = perf_counter()
    selected = _select_revisions([row for row in rows if not metrics or row.get("metric_id") in metrics], _as_of(as_of))
    selected = [row for row in selected if not metrics or row.get("metric_id") in metrics]
    _mark("financial_filter", perf_counter() - filter_started)
    if lookback > 0:
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for row in selected:
            by_metric.setdefault(row.get("metric_id", ""), []).append(row)
        selected = [row for metric_rows in by_metric.values() for row in metric_rows[-min(lookback, 40):]]
        selected.sort(key=lambda row: (row.get("period_end") or "", row.get("metric_id") or ""))
    if metrics and {row.get("metric_id") for row in selected} < set(metrics):
        raise ServingV2Error("METRIC_NOT_AVAILABLE", "one or more requested metrics are unavailable")
    served = [_serve_row(row, release_id=manifest["serving_release_id"], artifact_path=f"{key}/fundamentals/{source}.json", source_snapshot_id=manifest["source"]["fundamentals"]["snapshot_id"]) for row in selected]
    return {"world": {"world_type": "real", "world_id": "us-public-markets", "version": manifest["serving_release_id"]}, "entity": {"entity_id": snapshot["entity_id"], "symbol": snapshot["symbol"], "display_name": snapshot["symbol"]}, "observations": served, "release": {"serving_release_id": manifest["serving_release_id"], "source": manifest["source"], "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_hash": TEMPORAL_CONTRACT_SHA256}, "request_id": request_id}


async def price_history(env: Any, symbol: str, *, limit: int = 500, start_date: str | None = None, end_date: str | None = None, as_of: Any = None, request_id: str = "") -> dict[str, Any]:
    _, manifest = await load_release(env)
    resolved = await resolve_security(env, manifest, symbol, as_of)
    key = resolved["identity"]["artifact_path"]
    snapshot = await artifact(env, manifest, f"{key}/snapshot.json")
    prefix = f"{key}/prices/daily/"
    years = sorted({item["path"].split("/")[-1].removesuffix(".json") for item in manifest.get("artifacts", []) if item.get("path", "").startswith(prefix)})
    rows: list[dict[str, Any]] = []
    for year in reversed(years):
        if end_date and year > end_date[:4]:
            continue
        if start_date and year < start_date[:4]:
            break
        try:
            rows.extend(await artifact(env, manifest, f"{key}/prices/daily/{year}.json"))
        except ServingV2Error as exc:
            if exc.code != "SERVING_ARTIFACT_NOT_FOUND":
                raise
        if not start_date and not end_date and len(rows) >= limit:
            break
    rows = [row for row in rows if _available(row, _as_of(as_of)) and (not start_date or row.get("session_date", "") >= start_date) and (not end_date or row.get("session_date", "") <= end_date)]
    rows = sorted(rows, key=lambda row: row["session_date"])[-min(limit, 500):]
    for row in rows:
        row["provenance"] = {"serving_release_id": manifest["serving_release_id"], "source_snapshot_id": manifest["source"]["prices"]["snapshot_id"], "artifact": f"{key}/prices/daily/{row['session_date'][:4]}.json"}
    return {"world": {"world_type": "real", "world_id": "us-public-markets", "version": manifest["serving_release_id"]}, "prices": rows, "release": {"serving_release_id": manifest["serving_release_id"], "source": manifest["source"], "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_hash": TEMPORAL_CONTRACT_SHA256}, "request_id": request_id}


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
    resolved = await resolve_security(env, manifest, symbol, as_of)
    key = resolved["identity"]["artifact_path"]
    snapshot = await artifact(env, manifest, f"{key}/snapshot.json")
    row = snapshot.get("latest_price")
    if not row or not _available(row, _as_of(as_of)):
        history = await price_history(env, symbol, limit=1, as_of=as_of, request_id=request_id)
        if not history["prices"]:
            raise ServingV2Error("METRIC_NOT_AVAILABLE", "price is unavailable")
        row = history["prices"][-1]
    return {"world": {"world_type": "real", "world_id": "us-public-markets", "version": manifest["serving_release_id"]}, "metric": {"metric": "last_price", "value": row["close"], "unit": row["unit"], "period": row["session_date"], "available_at": row["available_at"], "volume": row.get("volume"), "evidence_id": row.get("evidence_id"), "provenance": {"serving_release_id": manifest["serving_release_id"], "source_snapshot_id": manifest["source"]["prices"]["snapshot_id"], "artifact": f"{key}/prices/daily/{row['session_date'][:4]}.json"}}, "release": {"serving_release_id": manifest["serving_release_id"], "source": manifest["source"], "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_hash": TEMPORAL_CONTRACT_SHA256}, "request_id": request_id}
