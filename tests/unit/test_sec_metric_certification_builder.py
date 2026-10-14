import importlib
import json
import sys

import pytest

pytest.importorskip("duckdb")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def api():
    return importlib.import_module("build_sec_metric_certification")


def raw_company(cost="6", gross="4", conflict=False):
    def concept(value):
        return {
            "units": {
                "USD": [
                    {
                        "start": "2024-01-01",
                        "end": "2024-12-31",
                        "val": int(value),
                        "accn": "0000000001-25-000001",
                        "filed": "2025-02-01",
                        "form": "10-K",
                        "fy": 2024,
                        "fp": "FY",
                    }
                ]
            }
        }

    facts = {
        "Revenues": concept("10"),
        "CostOfRevenue": concept(cost),
        "GrossProfit": concept(gross),
        "OperatingExpenses": concept("1"),
        "OperatingIncomeLoss": concept("3"),
    }
    if conflict:
        facts["CostOfGoodsAndServicesSold"] = concept("7")
    return json.dumps({"cik": 1, "entityName": "Synthetic", "facts": {"us-gaap": facts}}).encode()


def test_companion_normalization_exact_source_hash_and_metric_identities():
    builder = api()
    result = builder.build_companion(
        raw_company(),
        "0000000001",
        "release",
        "b" * 64,
        "2026-01-01T00:00:00Z",
        date_from="2024-01-01",
    )
    assert len(result["accounting_inputs"]) == 2
    assert result["certification"]["summary"]["ACCOUNTING_CERTIFIED"] == 2
    assert all(
        check["python_status"] == check["duckdb_status"] == "PASS"
        for check in result["identity_checks"]
    )
    cost = next(row for row in result["observations"] if row["metric"] == "cost_of_revenue")
    import hashlib

    from sec_engine import encoded

    expected = hashlib.sha256(
        encoded(
            {
                "cik": "0000000001",
                "concept": "CostOfRevenue",
                "unit": "USD",
                "source_record": cost["source_record"],
            }
        )
    ).hexdigest()
    assert cost["source_observation_id"] == expected
    assert cost["value_decimal"] == "6"
    assert result["reconciliation_evidence"]["implementation_sha256"]


def test_component_conflicts_never_get_accounting_inputs():
    result = api().build_companion(
        raw_company(conflict=True),
        "0000000001",
        "release",
        "b" * 64,
        "2026-01-01T00:00:00Z",
        date_from="2024-01-01",
    )
    assert not any(row["metric"] == "revenue" for row in result["accounting_inputs"])
    assert any(
        row["reason"] == "CONFLICTING_ACCOUNTING_COMPONENT_ALTERNATIVES"
        for row in result["accounting_gaps"]
    )


def test_actual_identity_imbalance_is_retained_as_failed_not_certified():
    result = api().build_companion(
        raw_company(gross="5"),
        "0000000001",
        "release",
        "b" * 64,
        "2026-01-01T00:00:00Z",
        date_from="2024-01-01",
    )
    assert all(
        row["status"] != "ACCOUNTING_CERTIFIED" for row in result["certification"]["observations"]
    )
    assert any(row["python_status"] == "FAIL" for row in result["identity_checks"])


def test_historical_extension_binds_member_and_exact_cutoff_without_replacing_current():
    builder = api()
    raw = raw_company()
    companion = builder.build_companion(
        raw, "0000000001", "release", "b" * 64, "2026-01-01T00:00:00Z", date_from="2024-01-01"
    )
    current = companion["reconciliation_evidence"]
    updated = builder.extend_historical(
        companion, raw, ["2025-03-01T00:00:00Z"], date_from="2024-01-01"
    )
    proof = updated["historical_reconciliation"]["2025-03-01T00:00:00Z"]
    assert proof["as_of"] == "2025-03-01T00:00:00Z" and proof["checks"]
    assert updated["reconciliation_evidence"] == current
    assert len({r["source_observation_id"] for r in updated["observations"]}) == len(
        updated["observations"]
    )
    with pytest.raises(ValueError, match="MEMBER"):
        builder.extend_historical(companion, raw + b" ", ["2025-03-01T00:00:00Z"])
