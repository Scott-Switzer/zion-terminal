from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from contract_v2 import (
    ENVELOPE_SCHEMA,
    OUTPUT_SCHEMA,
    SCHEMA_DRAFT,
    TOOLS as V2_TOOLS,
    WORLD_SCHEMA,
)
from temporal_core import TEMPORAL_CONTRACT_SHA256, TEMPORAL_SCHEMA_VERSION

CONTRACT_VERSION = "zion-tool-contract-v3"
RESPONSE_SCHEMA_VERSION = "zion-tool-response-v3"
TOOLS = copy.deepcopy(V2_TOOLS)
for definition in TOOLS.values():
    definition["supported_worlds"] = ["real", "synthetic"]


def canonical_manifest() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "schema_draft": SCHEMA_DRAFT,
        "world_schema": WORLD_SCHEMA,
        "envelope_schema": ENVELOPE_SCHEMA,
        "temporal_schema_version": TEMPORAL_SCHEMA_VERSION,
        "temporal_contract_sha256": TEMPORAL_CONTRACT_SHA256,
        "tools": TOOLS,
    }


def contract_bytes() -> bytes:
    return json.dumps(canonical_manifest(), sort_keys=True, separators=(",", ":")).encode()


def contract_sha256() -> str:
    return hashlib.sha256(contract_bytes()).hexdigest()


def release_block(release: dict[str, Any] | None, sha: str) -> dict[str, Any]:
    release = release or {}
    return {
        "serving_release_id": release.get("serving_release_id"),
        "synthetic_release_id": release.get("synthetic_release_id", release.get("release_id")),
        "temporal_schema_version": release.get("temporal_schema_version", TEMPORAL_SCHEMA_VERSION),
        "temporal_contract_sha256": release.get("temporal_contract_sha256", TEMPORAL_CONTRACT_SHA256),
        "tool_contract_version": CONTRACT_VERSION,
        "tool_contract_sha256": sha,
        "producer_release": release.get("producer_release"),
        "qc_certification_sha256": release.get("qc_certification_sha256"),
        "qc_status": release.get("qc_status"),
    }


def success(tool: str, data: Any, *, world: dict[str, Any], evidence: list[Any], quality: dict[str, Any], provenance: dict[str, Any], release: dict[str, Any] | None, request_id: str) -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "tool_contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "tool": tool,
        "world": world,
        "data": data,
        "evidence": evidence,
        "quality": quality,
        "provenance": provenance,
        "release": release_block(release, contract_sha256()),
    }
