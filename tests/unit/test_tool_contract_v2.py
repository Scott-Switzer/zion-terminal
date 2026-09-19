from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy" / "cloudflare-query" / "src"))

import pytest
import json

from contract_v2 import (
    CONTRACT_VERSION,
    TOOLS,
    ContractError,
    calculate_exact,
    contract_sha256,
    validate_object,
)


def test_generated_manifest_matches_registry_fingerprint():
    manifest = json.loads((Path(__file__).parents[2] / "contracts" / "zion-tool-contract-v2.json").read_text())
    sidecar = (Path(__file__).parents[2] / "contracts" / "zion-tool-contract-v2.sha256").read_text().strip()
    from contract_v2 import canonical_manifest
    assert manifest == canonical_manifest()
    assert sidecar == contract_sha256()


def test_registry_contains_exact_v2_tool_set_and_is_deterministic():
    assert CONTRACT_VERSION == "zion-tool-contract-v2"
    assert set(TOOLS) == {
        "resolve_entity", "resolve_security", "get_fundamentals", "get_price",
        "get_price_history", "get_corporate_actions", "get_revision_history",
        "get_evidence", "compare", "calculate", "screen",
    }
    assert len(contract_sha256()) == 64
    assert contract_sha256() == contract_sha256()
    assert all(item["read_only"] and item["deterministic"] for item in TOOLS.values())
    assert all("input_schema" in item for item in TOOLS.values())


def test_schema_rejects_unknown_and_missing_arguments():
    schema = TOOLS["get_price_history"]["input_schema"]
    with pytest.raises(ContractError, match="missing required"):
        validate_object({}, schema)
    with pytest.raises(ContractError, match="unknown field"):
        validate_object({"symbol": "MSFT", "bogus": 1}, schema)
    with pytest.raises(ContractError, match="above maximum"):
        validate_object({"symbol": "MSFT", "limit": 501}, schema)


def test_calculator_is_exact_decimal_and_bounded():
    assert calculate_exact("percent_change", ["100.00", "112.50"])["value_decimal"] == "0.125"
    assert calculate_exact("sum", ["0.1", "0.2"])["value_decimal"] == "0.3"
    with pytest.raises(ContractError) as exc:
        calculate_exact("ratio", ["1", "0"])
    assert exc.value.code == "CALCULATION_NOT_SUPPORTED"
