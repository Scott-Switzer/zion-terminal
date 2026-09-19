from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from temporal_core import TEMPORAL_CONTRACT_SHA256, TEMPORAL_SCHEMA_VERSION

CONTRACT_VERSION = "zion-tool-contract-v2"
RESPONSE_SCHEMA_VERSION = "zion-tool-response-v2"
SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"

WORLD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"world_type": {"type": "string", "enum": ["real", "synthetic"]}, "world_id": {"type": "string"}, "version": {"type": "string"}},
    "required": ["world_type", "world_id"],
}
ENVELOPE_SCHEMA = {
    "$schema": SCHEMA_DRAFT, "type": "object", "additionalProperties": False,
    "properties": {"world": WORLD_SCHEMA, "arguments": {"type": "object"}, "as_of": {"type": ["string", "null"]}, "serving_release_id": {"type": ["string", "null"]}},
    "required": ["world", "arguments"],
}


def _args(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"$schema": SCHEMA_DRAFT, "type": "object", "additionalProperties": False, "properties": properties, "required": required or []}

STRING = {"type": "string", "minLength": 1, "maxLength": 256}
DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 128}

TOOLS: dict[str, dict[str, Any]] = {
    "resolve_entity": {"title": "Resolve entity", "description": "Resolve an issuer entity by canonical ID, CIK, or symbol.", "input_schema": _args({"entity_id": IDENTIFIER, "cik": IDENTIFIER, "symbol": IDENTIFIER, "as_of": {"type": ["string", "null"]}}), "supported_worlds": ["real"],},
    "resolve_security": {"title": "Resolve security", "description": "Resolve a security and listing using point-in-time identifier validity.", "input_schema": _args({"symbol": IDENTIFIER, "instrument_id": IDENTIFIER, "listing_id": IDENTIFIER, "exchange": IDENTIFIER, "as_of": {"type": ["string", "null"]}}, ["symbol"]), "supported_worlds": ["real"],},
    "get_fundamentals": {"title": "Get fundamentals", "description": "Return entity-scoped exact financial observations with PIT filtering.", "input_schema": _args({"entity": IDENTIFIER, "symbol": IDENTIFIER, "metrics": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 20}, "period": {"type": "string", "enum": ["annual", "quarterly"]}, "lookback": {"type": "integer", "minimum": 1, "maximum": 40}}, ["metrics"]), "supported_worlds": ["real"],},
    "get_price": {"title": "Get price", "description": "Return the latest point-in-time listing price.", "input_schema": _args({"symbol": IDENTIFIER, "instrument_id": IDENTIFIER, "listing_id": IDENTIFIER, "as_of": {"type": ["string", "null"]}}, ["symbol"]), "supported_worlds": ["real"],},
    "get_price_history": {"title": "Get price history", "description": "Return bounded listing price history.", "input_schema": _args({"symbol": IDENTIFIER, "instrument_id": IDENTIFIER, "listing_id": IDENTIFIER, "start_date": DATE, "end_date": DATE, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, ["symbol"]), "supported_worlds": ["real"],},
    "get_corporate_actions": {"title": "Get corporate actions", "description": "Return evidence-backed corporate actions under PIT visibility.", "input_schema": _args({"symbol": IDENTIFIER, "entity_id": IDENTIFIER, "instrument_id": IDENTIFIER, "start": DATE, "end": DATE, "action_types": {"type": "array", "items": STRING, "maxItems": 20}}), "supported_worlds": ["real"],},
    "get_revision_history": {"title": "Get revision history", "description": "Return known revisions of one financial observation identity.", "input_schema": _args({"entity": IDENTIFIER, "symbol": IDENTIFIER, "metric": STRING, "period_type": {"type": "string", "enum": ["annual", "quarterly"]}, "period_start": DATE, "period_end": DATE}, ["metric"]), "supported_worlds": ["real"],},
    "get_evidence": {"title": "Get evidence", "description": "Retrieve canonical provenance by evidence ID.", "input_schema": _args({"evidence_id": IDENTIFIER, "observation_id": IDENTIFIER, "action_id": IDENTIFIER}, ["evidence_id"]), "supported_worlds": ["real"],},
    "compare": {"title": "Compare", "description": "Compare a bounded metric series across entities.", "input_schema": _args({"entities": {"type": "array", "items": IDENTIFIER, "minItems": 1, "maxItems": 10}, "metric": STRING, "period": {"type": "string", "enum": ["annual", "quarterly"]}, "lookback": {"type": "integer", "minimum": 1, "maximum": 40}}, ["entities", "metric"]), "supported_worlds": ["real"],},
    "calculate": {"title": "Calculate", "description": "Perform a bounded exact-decimal calculation.", "input_schema": _args({"operation": {"type": "string", "enum": ["difference", "percent_change", "average", "sum", "ratio"]}, "values": {"type": "array", "items": {"type": ["string", "number"]}, "minItems": 1, "maxItems": 40}, "unit": STRING}, ["operation", "values"]), "supported_worlds": ["real", "synthetic"],},
    "screen": {"title": "Screen", "description": "Run a bounded deterministic screen over the materialized serving universe.", "input_schema": _args({"filters": {"type": "array", "maxItems": 8, "items": {"type": "object", "additionalProperties": False, "properties": {"field": STRING, "operator": {"type": "string", "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "between"]}, "value": {}, "values": {"type": "array", "maxItems": 2}}, "required": ["field", "operator"]}}, "sort": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False, "properties": {"field": STRING, "direction": {"type": "string", "enum": ["asc", "desc"]}}, "required": ["field"]}}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["filters"]), "supported_worlds": ["real"],},
}

OUTPUT_SCHEMA = {
    "$schema": SCHEMA_DRAFT, "type": "object", "additionalProperties": True,
    "required": ["schema_version", "tool_contract_version", "request_id", "tool", "world", "data", "evidence", "quality", "provenance", "release"],
}

for definition in TOOLS.values():
    definition.update({"read_only": True, "deterministic": True, "output_schema": OUTPUT_SCHEMA})


def canonical_manifest() -> dict[str, Any]:
    return {"contract_version": CONTRACT_VERSION, "response_schema_version": RESPONSE_SCHEMA_VERSION, "schema_draft": SCHEMA_DRAFT, "world_schema": WORLD_SCHEMA, "envelope_schema": ENVELOPE_SCHEMA, "tools": TOOLS}


def contract_bytes() -> bytes:
    return json.dumps(canonical_manifest(), sort_keys=True, separators=(",", ":")).encode()


def contract_sha256() -> str:
    return hashlib.sha256(contract_bytes()).hexdigest()


def validate_object(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    if not isinstance(value, dict):
        raise ContractError("INVALID_REQUEST", f"{path} must be an object")
    required = schema.get("required", [])
    for key in required:
        if key not in value:
            raise ContractError("INVALID_ARGUMENT", f"missing required field: {path}.{key}")
    if schema.get("additionalProperties") is False:
        unknown = set(value) - set(schema.get("properties", {}))
        if unknown:
            raise ContractError("INVALID_ARGUMENT", f"unknown field: {path}.{sorted(unknown)[0]}")
    for key, child in schema.get("properties", {}).items():
        if key not in value or value[key] is None:
            continue
        _validate(value[key], child, f"{path}.{key}")


def _validate(value: Any, schema: dict[str, Any], path: str) -> None:
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        ok = any((t == "object" and isinstance(value, dict)) or (t == "array" and isinstance(value, list)) or (t == "string" and isinstance(value, str)) or (t == "integer" and isinstance(value, int) and not isinstance(value, bool)) or (t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)) or (t == "null" and value is None) for t in types)
        if not ok: raise ContractError("INVALID_ARGUMENT", f"invalid type at {path}")
    if isinstance(value, dict): validate_object(value, schema, path)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]: raise ContractError("INVALID_ARGUMENT", f"too few items at {path}")
        if "maxItems" in schema and len(value) > schema["maxItems"]: raise ContractError("INVALID_ARGUMENT", f"too many items at {path}")
        for i, item in enumerate(value): _validate(item, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]: raise ContractError("INVALID_ARGUMENT", f"empty value at {path}")
        if "maxLength" in schema and len(value) > schema["maxLength"]: raise ContractError("INVALID_ARGUMENT", f"value too long at {path}")
    if isinstance(value, int) and "minimum" in schema and value < schema["minimum"]: raise ContractError("INVALID_ARGUMENT", f"value below minimum at {path}")
    if isinstance(value, int) and "maximum" in schema and value > schema["maximum"]: raise ContractError("INVALID_ARGUMENT", f"value above maximum at {path}")
    if "enum" in schema and value not in schema["enum"]: raise ContractError("INVALID_ARGUMENT", f"unsupported value at {path}")


class ContractError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int = 400):
        self.code, self.message, self.retryable, self.status = code, message, retryable, status
        super().__init__(message)


def envelope(arguments: dict[str, Any], world: dict[str, Any] | None = None, as_of: Any = None, release: Any = None) -> dict[str, Any]:
    return {"world": world or {"world_type": "real", "world_id": "us-public-markets"}, "arguments": arguments, "as_of": as_of, "serving_release_id": release}


def release_block(release: dict[str, Any] | None, sha: str) -> dict[str, Any]:
    release = release or {}
    return {"serving_release_id": release.get("serving_release_id", release.get("release_id")), "temporal_schema_version": release.get("temporal_schema_version", TEMPORAL_SCHEMA_VERSION), "temporal_contract_sha256": release.get("temporal_contract_sha256", release.get("temporal_contract_hash", TEMPORAL_CONTRACT_SHA256)), "tool_contract_version": CONTRACT_VERSION, "tool_contract_sha256": sha, "source_snapshot_id": (release.get("source") or {}).get("fundamentals", {}).get("snapshot_id"), "producer_release": release.get("producer_release")}


def success(tool: str, data: Any, *, world: dict[str, Any], evidence: list[Any], quality: dict[str, Any], provenance: dict[str, Any], release: dict[str, Any] | None, request_id: str, sha: str) -> dict[str, Any]:
    return {"schema_version": RESPONSE_SCHEMA_VERSION, "tool_contract_version": CONTRACT_VERSION, "request_id": request_id, "tool": tool, "world": world, "data": data, "evidence": evidence, "quality": quality, "provenance": provenance, "release": release_block(release, sha)}


def calculate_exact(operation: str, values: list[Any]) -> dict[str, Any]:
    try: nums = [Decimal(str(value)) for value in values]
    except (InvalidOperation, ValueError) as exc: raise ContractError("INVALID_ARGUMENT", "values must be exact decimal numbers") from exc
    if operation == "difference" and len(nums) == 2: result = nums[1] - nums[0]; formula = "second - first"
    elif operation == "percent_change" and len(nums) == 2:
        if nums[0] == 0: raise ContractError("CALCULATION_NOT_SUPPORTED", "percent change denominator cannot be zero", status=422)
        result = (nums[1] - nums[0]) / nums[0]; formula = "(second - first) / first"
    elif operation == "average" and nums: result = sum(nums) / Decimal(len(nums)); formula = "sum(values) / count(values)"
    elif operation == "sum" and nums: result = sum(nums); formula = "sum(values)"
    elif operation == "ratio" and len(nums) == 2:
        if nums[1] == 0: raise ContractError("CALCULATION_NOT_SUPPORTED", "ratio denominator cannot be zero", status=422)
        result = nums[0] / nums[1]; formula = "first / second"
    else: raise ContractError("CALCULATION_NOT_SUPPORTED", "operation requires supported input count", status=422)
    return {"operation": operation, "value_decimal": format(result, "f"), "formula": formula, "inputs": [format(value, "f") for value in nums]}
