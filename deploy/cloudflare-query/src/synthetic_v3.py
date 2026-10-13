from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


RELEASE_ROOT = "gold/synthetic-worlds/releases"
REQUIRED_PUBLIC = ("entities.json", "financials.json", "prices.json", "events.json")
HEX_RELEASE = re.compile(r"^[a-f0-9]{64}$")
FORBIDDEN_PUBLIC_KEYS = {
    "hidden_truth", "latents", "latent_demand_index", "private_event", "world_spec",
    "agent_states", "counterfactual", "counterfactuals", "causal_notes", "fraud_windows",
    "generator_parameters", "management_state", "future_outcomes", "hidden_artifacts",
}


class SyntheticReleaseError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 404, retryable: bool = False):
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        super().__init__(message)


async def _text(env: Any, key: str) -> str:
    obj = await env.MARKET_DATA.get(key)
    if obj is None:
        raise SyntheticReleaseError("SYNTHETIC_RELEASE_UNAVAILABLE", "synthetic release artifact is unavailable", status=503, retryable=True)
    try:
        return await obj.text()
    except Exception as exc:
        raise SyntheticReleaseError("SYNTHETIC_RELEASE_UNAVAILABLE", "synthetic release artifact could not be read", status=503, retryable=True) from exc


async def _json(env: Any, key: str) -> tuple[dict[str, Any], bytes]:
    raw = (await _text(env, key)).encode()
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", "synthetic release JSON is invalid", status=503) from exc
    if not isinstance(value, dict):
        raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", "synthetic release artifact must be an object", status=503)
    return value, raw


def _safe_world_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise SyntheticReleaseError("WORLD_NOT_FOUND", "synthetic world is not available")
    return value


def _world_equal(left: Any, right: dict[str, Any]) -> bool:
    return isinstance(left, dict) and left == right


def _walk_forbidden(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_PUBLIC_KEYS:
                return str(key)
            found = _walk_forbidden(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_forbidden(child)
            if found:
                return found
    return None


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", f"public {field} timestamp is invalid", status=503)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", f"public {field} timestamp is invalid", status=503) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_of(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _parse_time(value, "as_of")


def _visible(row: dict[str, Any], as_of: datetime | None) -> bool:
    if as_of is None:
        return True
    available = row.get("available_at") or row.get("filed_at") or row.get("at") or row.get("observation_at")
    return _parse_time(available, "available_at") <= as_of


def _evidence_id(world_id: str, kind: str, row: dict[str, Any]) -> str:
    stable = {
        "world_id": world_id,
        "kind": kind,
        "entity_id": row.get("entity_id"),
        "security": row.get("security"),
        "metric": row.get("metric"),
        "period": row.get("period"),
        "period_end": row.get("period_end"),
        "session": row.get("session"),
        "at": row.get("at"),
        "available_at": row.get("available_at"),
        "source_record": (row.get("provenance") or {}).get("source_record") or row.get("source_record"),
    }
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"synthetic:{kind}:{digest}"


def _period_matches(value: Any, requested: Any) -> bool:
    if requested in (None, ""):
        return True
    if not isinstance(value, str):
        return False
    if requested == "quarterly":
        return "Q" in value.upper() or value.upper().startswith("QUARTER")
    if requested == "annual":
        return value.upper().startswith("FY") or value.upper().startswith("ANNUAL") or "Q" not in value.upper()
    return value == requested


def _date_matches(value: Any, start: Any, end: Any) -> bool:
    if not isinstance(value, str):
        return False
    day = value[:10]
    if start and day < str(start):
        return False
    if end and day > str(end):
        return False
    return True


class SyntheticRelease:
    def __init__(self, *, env: Any, world: dict[str, Any], release_id: str, prefix: str, manifest: dict[str, Any], qc: dict[str, Any], entities: list[dict[str, Any]], financials: list[dict[str, Any]], prices: list[dict[str, Any]], events: list[dict[str, Any]]):
        self.env = env
        self.world = world
        self.release_id = release_id
        self.prefix = prefix
        self.manifest = manifest
        self.qc = qc
        self.entities = entities
        self.financials = financials
        self.prices = prices
        self.events = events
        self.entity_by_symbol = {str(row.get("symbol", "")).upper(): row for row in entities}
        self.entity_by_id = {row.get("entity_id"): row for row in entities}

    @property
    def release(self) -> dict[str, Any]:
        producer = self.manifest.get("producer") or {}
        return {
            "synthetic_release_id": self.release_id,
            "release_id": self.release_id,
            "world_version": self.world.get("version"),
            "producer_release": producer,
            "producer_git_sha": producer.get("git_sha"),
            "qc_status": "PASS",
            "qc_certification_sha256": self.manifest.get("qc_report_sha256"),
            "qc_validator_version": self.manifest.get("qc_validator_version"),
        }

    def entity(self, identifier: Any) -> dict[str, Any]:
        if not isinstance(identifier, str) or not identifier:
            raise SyntheticReleaseError("INVALID_ARGUMENT", "synthetic entity identifier is required", status=400)
        item = self.entity_by_id.get(identifier) or self.entity_by_symbol.get(identifier.upper())
        if item is None:
            raise SyntheticReleaseError("ENTITY_NOT_FOUND", "entity is not in the synthetic world")
        return dict(item)

    def normalized_financials(self) -> list[dict[str, Any]]:
        output = []
        for row in self.financials:
            item = dict(row)
            item["evidence_id"] = item.get("evidence_id") or _evidence_id(self.world["world_id"], "financial", item)
            item["world"] = self.world
            item["provenance"] = {**(item.get("provenance") or {}), "release_id": self.release_id, "synthetic_release_id": self.release_id, "qc_certification_sha256": self.manifest.get("qc_report_sha256")}
            item["quality"] = {**(item.get("quality") or {}), "qc_status": "PASS", "visibility": "public"}
            output.append(item)
        return output

    def normalized_prices(self) -> list[dict[str, Any]]:
        output = []
        for row in self.prices:
            item = dict(row)
            item["evidence_id"] = item.get("evidence_id") or _evidence_id(self.world["world_id"], "price", item)
            item["world"] = self.world
            item["provenance"] = {**(item.get("provenance") or {}), "release_id": self.release_id, "synthetic_release_id": self.release_id, "qc_certification_sha256": self.manifest.get("qc_report_sha256")}
            item["quality"] = {**(item.get("quality") or {}), "qc_status": "PASS", "visibility": "public"}
            output.append(item)
        return output

    def normalized_events(self) -> list[dict[str, Any]]:
        output = []
        for row in self.events:
            item = dict(row)
            item["action_id"] = item.get("action_id") or _evidence_id(self.world["world_id"], "event", item)
            item["evidence_id"] = item.get("evidence_id") or item["action_id"]
            item["available_at"] = item.get("available_at") or item.get("at")
            item["world"] = self.world
            item["provenance"] = {**(item.get("provenance") or {}), "release_id": self.release_id, "synthetic_release_id": self.release_id, "qc_certification_sha256": self.manifest.get("qc_report_sha256")}
            item["quality"] = {**(item.get("quality") or {}), "qc_status": "PASS", "visibility": "public"}
            output.append(item)
        return output

    def financial_rows(self, entity: dict[str, Any], metrics: list[str], *, period: Any = None, as_of: Any = None, revisions: bool = False) -> list[dict[str, Any]]:
        cutoff = _as_of(as_of)
        rows = [row for row in self.normalized_financials() if row.get("entity_id") == entity.get("entity_id") and row.get("metric") in metrics and _period_matches(row.get("period"), period) and _visible(row, cutoff)]
        if revisions:
            return sorted(rows, key=lambda row: (row.get("period_end", ""), row.get("available_at", ""), row.get("metric", "")))
        selected: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            key = (row.get("metric"), row.get("period"), row.get("period_start"), row.get("period_end"), row.get("unit"))
            if key not in selected or (row.get("available_at", ""), row.get("retrieved_at", "")) > (selected[key].get("available_at", ""), selected[key].get("retrieved_at", "")):
                selected[key] = row
        return sorted(selected.values(), key=lambda row: (row.get("period_end", ""), row.get("metric", "")))

    def fundamentals(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        entity = self.entity(args.get("entity") or args.get("symbol"))
        metrics = args.get("metrics") or [args.get("metric", "revenue")]
        rows = self.financial_rows(entity, metrics, period=args.get("period"), as_of=args.get("as_of"))
        lookback = min(int(args.get("lookback", 40)), 40)
        if lookback > 0:
            periods = sorted({(row.get("period_end", ""), row.get("period", "")) for row in rows})[-lookback:]
            rows = [row for row in rows if (row.get("period_end", ""), row.get("period", "")) in periods]
        if not rows:
            raise SyntheticReleaseError("METRIC_NOT_AVAILABLE", "requested synthetic fundamentals are not available")
        return rows

    def latest_price(self, args: dict[str, Any]) -> dict[str, Any]:
        entity = self.entity(args.get("symbol") or args.get("entity"))
        cutoff = _as_of(args.get("as_of"))
        rows = [row for row in self.normalized_prices() if row.get("entity_id") == entity.get("entity_id") and _visible(row, cutoff)]
        if not rows:
            raise SyntheticReleaseError("METRIC_NOT_AVAILABLE", "requested synthetic price is not available")
        return max(rows, key=lambda row: (row.get("session", ""), row.get("observation_at", "")))

    def price_history(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        entity = self.entity(args.get("symbol") or args.get("entity"))
        cutoff = _as_of(args.get("as_of"))
        rows = [row for row in self.normalized_prices() if row.get("entity_id") == entity.get("entity_id") and _visible(row, cutoff) and _date_matches(row.get("session"), args.get("start_date"), args.get("end_date"))]
        rows.sort(key=lambda row: (row.get("session", ""), row.get("observation_at", "")))
        return rows[-min(int(args.get("limit", 500)), 500):]

    def corporate_actions(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        identifier = args.get("entity_id") or args.get("instrument_id") or args.get("symbol")
        entity = self.entity(identifier) if identifier else None
        cutoff = _as_of(args.get("as_of"))
        action_types = set(args.get("action_types") or [])
        rows = []
        for event in self.normalized_events():
            if entity and event.get("entity_id") != entity.get("entity_id"):
                continue
            if action_types and event.get("kind") not in action_types:
                continue
            if not _date_matches(event.get("at"), args.get("start"), args.get("end")) or not _visible(event, cutoff):
                continue
            rows.append(event)
        rows.sort(key=lambda row: row.get("at", ""))
        return rows

    def evidence(self, evidence_id: str) -> list[dict[str, Any]]:
        matches = []
        for row in self.normalized_financials() + self.normalized_prices() + self.normalized_events():
            if evidence_id in {row.get("evidence_id"), row.get("action_id")}:
                matches.append(row)
        if not matches:
            raise SyntheticReleaseError("EVIDENCE_NOT_FOUND", "evidence was not found in the synthetic public release")
        return matches

    def revisions(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        entity = self.entity(args.get("entity") or args.get("symbol"))
        rows = self.financial_rows(entity, [args["metric"]], period=args.get("period_type"), as_of=args.get("as_of"), revisions=True)
        if args.get("period_start"):
            rows = [row for row in rows if row.get("period_start") == args["period_start"]]
        if args.get("period_end"):
            rows = [row for row in rows if row.get("period_end") == args["period_end"]]
        return rows

    def screen(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        cutoff = _as_of(args.get("as_of"))
        financials = self.normalized_financials()
        prices = self.normalized_prices()
        by_entity: dict[str, dict[str, Any]] = {}
        for row in financials:
            if not _visible(row, cutoff):
                continue
            key = row.get("entity_id")
            current = by_entity.setdefault(key, {})
            field = row.get("metric")
            identity = (row.get("period_end", ""), row.get("available_at", ""))
            if field not in current or identity > current[field]["_identity"]:
                current[field] = {"value": row.get("value"), "evidence_id": row.get("evidence_id"), "_identity": identity}
        for row in prices:
            if not _visible(row, cutoff):
                continue
            key = row.get("entity_id")
            current = by_entity.setdefault(key, {})
            identity = (row.get("session", ""), row.get("available_at", ""))
            if "last_price" not in current or identity > current["last_price"]["_identity"]:
                current["last_price"] = {"value": row.get("close"), "evidence_id": row.get("evidence_id"), "_identity": identity}
        requested = {item.get("field") for item in args.get("filters", [])} | {item.get("field") for item in args.get("sort", [])}
        supported = {"revenue", "gross_profit", "operating_income", "net_income", "operating_margin", "gross_margin", "last_price", "assets", "liabilities", "equity", "cash", "debt"}
        unsupported = sorted(field for field in requested if field not in supported)
        if unsupported:
            raise SyntheticReleaseError("METRIC_NOT_AVAILABLE", f"screen field is not materialized: {unsupported[0]}", status=422)
        results = []
        for entity_id, values in by_entity.items():
            entity = self.entity_by_id.get(entity_id)
            if not entity:
                continue
            public_values = {field: {key: value for key, value in item.items() if key != "_identity"} for field, item in values.items()}
            def matches(filter_item: dict[str, Any]) -> bool:
                item = values.get(filter_item.get("field")); value = item.get("value") if item else None
                if value is None:
                    return False
                try:
                    left = Decimal(str(value)); op = filter_item.get("operator")
                    if op == "between":
                        bounds = filter_item.get("values") or []
                        return len(bounds) == 2 and Decimal(str(bounds[0])) <= left <= Decimal(str(bounds[1]))
                    right = Decimal(str(filter_item.get("value")))
                    return {"eq": left == right, "ne": left != right, "gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[op]
                except (KeyError, TypeError, InvalidOperation, ValueError):
                    return False
            if all(matches(item) for item in args.get("filters", [])):
                results.append({"symbol": entity.get("symbol"), "entity_id": entity_id, "values": public_values, "evidence": [item["evidence_id"] for item in values.values() if item.get("evidence_id")]})
        for item in reversed(args.get("sort", [])):
            field = item.get("field")
            results.sort(key=lambda row: (row["values"].get(field, {}).get("value") is None, row["values"].get(field, {}).get("value")), reverse=item.get("direction", "desc") == "desc")
        return results[:min(int(args.get("limit", 100)), 100)]


class SyntheticReleaseAdapter:
    def __init__(self, env: Any):
        self.env = env

    def allowed_world_ids(self) -> list[str]:
        raw = getattr(self.env, "SYNTHETIC_WORLD_IDS", "") or getattr(self.env, "SYNTHETIC_WORLD_ID", "")
        return [_safe_world_id(item.strip()) for item in str(raw).split(",") if item.strip()]

    async def read_pointer(self, world_id: str) -> dict[str, Any]:
        configured = self.allowed_world_ids()
        if world_id not in configured:
            raise SyntheticReleaseError("WORLD_NOT_FOUND", "synthetic world is not available")
        key = getattr(self.env, "SYNTHETIC_CURRENT_KEY", f"control/synthetic-worlds/{world_id}/CURRENT.json")
        value, _ = await _json(self.env, key)
        if value.get("world_id") != world_id:
            raise SyntheticReleaseError("WORLD_RELEASE_MISMATCH", "synthetic CURRENT pointer world does not match request", status=503)
        return value

    async def load(self, world: dict[str, Any], pinned_release_id: str | None = None) -> SyntheticRelease:
        if not isinstance(world, dict) or world.get("world_type") != "synthetic":
            raise SyntheticReleaseError("WORLD_NOT_FOUND", "synthetic world is not available")
        world_id = _safe_world_id(world.get("world_id"))
        pointer = None if pinned_release_id else await self.read_pointer(world_id)
        release_id = pinned_release_id or (pointer or {}).get("release_id")
        if not isinstance(release_id, str) or not HEX_RELEASE.fullmatch(release_id):
            raise SyntheticReleaseError("WORLD_RELEASE_MISMATCH", "synthetic release identity is invalid", status=503)
        prefix = f"{RELEASE_ROOT}/{release_id}"
        if pointer is not None and pointer.get("prefix") != prefix:
            raise SyntheticReleaseError("WORLD_RELEASE_MISMATCH", "synthetic CURRENT pointer release does not match identity", status=503)
        manifest, manifest_raw = await _json(self.env, f"{prefix}/manifest.json")
        if hashlib.sha256(manifest_raw).hexdigest() != release_id:
            raise SyntheticReleaseError("WORLD_RELEASE_MISMATCH", "synthetic manifest hash does not match release identity", status=503)
        manifest_world = manifest.get("world")
        if not isinstance(manifest_world, dict) or manifest_world.get("world_type") != "synthetic" or manifest_world.get("world_id") != world_id:
            raise SyntheticReleaseError("WORLD_RELEASE_MISMATCH", "synthetic manifest world does not match request", status=503)
        if world.get("version") and world.get("version") != manifest_world.get("version"):
            raise SyntheticReleaseError("WORLD_RELEASE_MISMATCH", "synthetic world version does not match release", status=503)
        if manifest.get("qc_status") != "PASS" or not manifest.get("qc_report_sha256"):
            raise SyntheticReleaseError("WORLD_NOT_CERTIFIED", "synthetic world does not have a PASS QC certification", status=503)
        qc, qc_raw = await _json(self.env, f"{prefix}/qc_certification.json")
        if hashlib.sha256(qc_raw).hexdigest() != manifest.get("qc_report_sha256") or qc.get("status") != "PASS" or qc.get("world_id") != world_id:
            raise SyntheticReleaseError("WORLD_NOT_CERTIFIED", "synthetic QC certification is invalid", status=503)
        entities_payload, _ = await _json(self.env, f"{prefix}/public/entities.json")
        financials_payload, _ = await _json(self.env, f"{prefix}/public/financials.json")
        prices_payload, _ = await _json(self.env, f"{prefix}/public/prices.json")
        events_payload, _ = await _json(self.env, f"{prefix}/public/events.json")
        payloads = (entities_payload, financials_payload, prices_payload, events_payload)
        forbidden = next((found for payload in payloads if (found := _walk_forbidden(payload))), None)
        if forbidden:
            raise SyntheticReleaseError("PUBLIC_ARTIFACT_INVALID", f"public artifact contains hidden-only field: {forbidden}", status=503)
        hashes = manifest.get("artifact_hashes")
        if not isinstance(hashes, dict):
            raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", "synthetic artifact hashes are missing", status=503)
        for name, payload, raw in (("entities.json", entities_payload, None), ("financials.json", financials_payload, None), ("prices.json", prices_payload, None), ("events.json", events_payload, None)):
            key = f"public/{name}"
            expected = hashes.get(key, {}).get("sha256") if isinstance(hashes.get(key), dict) else None
            if not expected:
                raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", f"required public artifact hash is missing: {key}", status=503)
            artifact_raw = (await _text(self.env, f"{prefix}/{key}")).encode()
            if hashlib.sha256(artifact_raw).hexdigest() != expected:
                raise SyntheticReleaseError("SYNTHETIC_ARTIFACT_CORRUPT", f"public artifact hash mismatch: {key}", status=503)
            if not isinstance(payload, dict):
                raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", f"public artifact is invalid: {key}", status=503)
        if not isinstance(entities_payload.get("entities"), list) or not isinstance(financials_payload.get("observations"), list) or not isinstance(prices_payload.get("prices"), list) or not isinstance(events_payload.get("events"), list):
            raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", "required public artifact collections are invalid", status=503)
        for payload in payloads:
            if payload.get("world") != manifest_world:
                raise SyntheticReleaseError("WORLD_RELEASE_MISMATCH", "public artifact world does not match manifest", status=503)
        entities = entities_payload["entities"]
        entity_ids = {item.get("entity_id") for item in entities if isinstance(item, dict)}
        if not entity_ids or any(not isinstance(item, dict) or item.get("world") != manifest_world or item.get("entity_id") not in entity_ids for item in entities):
            raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", "synthetic entity artifact is inconsistent", status=503)
        for collection_name, collection in (("financials", financials_payload["observations"]), ("prices", prices_payload["prices"])):
            for row in collection:
                if not isinstance(row, dict) or row.get("entity_id") not in entity_ids or row.get("world") != manifest_world:
                    raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", f"synthetic {collection_name} artifact is inconsistent", status=503)
                _parse_time(row.get("available_at"), "available_at")
        for row in events_payload["events"]:
            if not isinstance(row, dict) or row.get("entity_id") not in entity_ids or row.get("world") != manifest_world:
                raise SyntheticReleaseError("SYNTHETIC_RELEASE_INVALID", "synthetic event artifact is inconsistent", status=503)
            _parse_time(row.get("available_at") or row.get("at"), "available_at")
        return SyntheticRelease(env=self.env, world=manifest_world, release_id=release_id, prefix=prefix, manifest=manifest, qc=qc, entities=entities, financials=financials_payload["observations"], prices=prices_payload["prices"], events=events_payload["events"])
