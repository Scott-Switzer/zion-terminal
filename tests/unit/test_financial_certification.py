import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/cloudflare-query/src"))


def api():
    return importlib.import_module("financial_certification")


def leaf(
    sid,
    metric,
    value,
    *,
    concept=None,
    accession="0000000001-25-000001",
    unit="USD",
    start="2024-01-01",
    end="2024-12-31",
    available="2025-02-02T05:00:00Z",
):
    return {
        "source_observation_id": sid,
        "cik": "0000000001",
        "metric": metric,
        "concept": concept
        or {
            "revenue": "us-gaap:Revenues",
            "operating_income": "us-gaap:OperatingIncomeLoss",
            "operating_cash_flow": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            "capital_expenditures": "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        }.get(metric, "us-gaap:" + metric),
        "value_decimal": value,
        "unit": unit,
        "currency": "USD" if unit == "USD" else None,
        "period_start": start,
        "period_end": end,
        "available_at": available,
        "accession": accession,
        "form": "10-K",
        "member_sha256": "a" * 64,
    }


def setup(rows, targets=None):
    data = {
        "rights": "PUBLIC_SEC_ONLY",
        "cik": "0000000001",
        "member_sha256": "a" * 64,
        "registry_sha256": "b" * 64,
        "policy": {"quarantined_metrics": {}},
        "observations": rows,
    }
    observations = [
        {
            **r,
            "frequency": "annual",
            "source_observation_ids": [r["source_observation_id"]],
            "source_terms": [
                {"source_observation_id": r["source_observation_id"], "coefficient": 1}
            ],
        }
        for r in (targets if targets is not None else rows)
    ]
    return data, {"observations": observations, "coverage_gaps": []}


def certify(data, history, **kwargs):
    return api().certify_history(data, history, as_of="2026-01-01T00:00:00Z", **kwargs)


def test_source_observed_is_not_accounting_certified_by_balance_pass():
    data, history = setup([leaf("r", "revenue", "9007199254740993")])
    history["accounting_checks"] = [
        {"status": "PASS", "identity": "Assets = LiabilitiesAndStockholdersEquity"}
    ]
    result = certify(data, history)
    assert result["observations"][0]["status"] == "SOURCE_OBSERVED"
    assert "ACCOUNTING_CERTIFIED" not in result["observations"][0]["verified_statuses"]
    assert result["observations"][0]["source_evidence"][0]["source_id"] == "r"


def test_exact_derivation_verified_but_not_accounting_certified():
    ocf = leaf("o", "operating_cash_flow", "9007199254740994")
    capex = leaf("c", "capital_expenditures", "1")
    data, history = setup([ocf, capex], [])
    history["observations"] = [
        {
            "metric": "free_cash_flow",
            "value_decimal": "9007199254740993",
            "unit": "USD",
            "currency": "USD",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "frequency": "annual",
            "record_kind": "DERIVED",
            "formula": "SUBTRACT",
            "source_observation_ids": ["o", "c"],
            "source_terms": [
                {"source_observation_id": "o", "coefficient": 1},
                {"source_observation_id": "c", "coefficient": -1},
            ],
        }
    ]
    row = certify(data, history)["observations"][0]
    assert row["status"] == "DERIVATION_VERIFIED" and row["accounting_status"] == "NOT_ESTABLISHED"
    history["observations"][0]["value_decimal"] = "9007199254740992"
    assert certify(data, history)["observations"][0]["status"] == "UNAVAILABLE"


def test_independent_reconciliation_needs_exact_row_and_source_binding():
    data, history = setup([leaf("r", "revenue", "10")])
    evidence = {
        "method": "INDEPENDENT_SQL_DECIMAL",
        "implementation_sha256": "c" * 64,
        "member_sha256": "a" * 64,
        "registry_sha256": "b" * 64,
        "checks": [
            {
                "status": "PASS",
                "metric": "revenue",
                "frequency": "annual",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "unit": "USD",
                "value_decimal": "10",
                "source_ids": ["r"],
            }
        ],
    }
    assert (
        certify(data, history, reconciliation_evidence=evidence)["observations"][0]["status"]
        == "INDEPENDENTLY_RECONCILED"
    )
    evidence["checks"][0]["value_decimal"] = "11"
    assert (
        certify(data, history, reconciliation_evidence=evidence)["observations"][0]["status"]
        == "SOURCE_OBSERVED"
    )


def test_metric_specific_same_filing_identity_certifies_revenue_only():
    revenue = leaf("r", "revenue", "10")
    cost = leaf("c", "cost_of_revenue", "6")
    gross = leaf("g", "gross_profit", "4")
    data, history = setup([revenue, cost, gross], [revenue])
    identity = {
        "identity_id": "REVENUE_MINUS_COST_EQUALS_GROSS_PROFIT",
        "metric": "revenue",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "component_ids": {"revenue": "r", "cost_of_revenue": "c", "gross_profit": "g"},
    }
    result = certify(data, history, accounting_inputs=[identity])["observations"][0]
    assert result["status"] == "ACCOUNTING_CERTIFIED"
    cost["accession"] = "0000000001-24-000001"
    result = certify(data, history, accounting_inputs=[identity])["observations"][0]
    assert result["status"] != "ACCOUNTING_CERTIFIED"
    assert "ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH" in result["reasons"]


def test_missing_conflicted_industry_and_null_are_unavailable():
    source = leaf("r", "revenue", "10")
    data, history = setup([source])
    data["policy"]["quarantined_metrics"]["revenue"] = (
        "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED"
    )
    assert certify(data, history)["observations"][0]["status"] == "UNAVAILABLE"
    data["policy"]["quarantined_metrics"] = {}
    data["observations"].append(
        {**source, "source_observation_id": "conflict", "value_decimal": "11"}
    )
    assert certify(data, history)["observations"][0]["status"] == "UNAVAILABLE"
    data["observations"] = [source]
    history["observations"][0]["value_decimal"] = None
    assert certify(data, history)["observations"][0]["status"] == "UNAVAILABLE"
    history["coverage_gaps"] = [
        {"metric": "revenue", "period_end": "2025-12-31", "reason": "SOURCE_MISSING"}
    ]
    assert certify(data, history)["coverage_gaps"][0]["status"] == "UNAVAILABLE"


def test_licensed_data_rejected_before_evidence_processing():
    data, history = setup([leaf("r", "revenue", "10")])
    data["rights"] = "LICENSED_PRIVATE"
    with pytest.raises(ValueError, match="PUBLIC_SEC_ONLY"):
        certify(data, history)


def test_cash_movement_same_filing_scope_and_rounding_shares():
    ocf = leaf("o", "operating_cash_flow", "10")
    rows = [
        ocf,
        leaf("i", "investing_cash_flow", "-4"),
        leaf("f", "financing_cash_flow", "-2"),
        leaf("x", "fx_cash_effect", "1"),
        leaf("b", "cash", "20", start=None, end="2023-12-31"),
        leaf("e", "cash", "25", start=None),
    ]
    data, history = setup(rows, [ocf])
    evidence = {
        "identity_id": "CASH_MOVEMENT",
        "metric": "operating_cash_flow",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "component_ids": {
            "operating_cash_flow": "o",
            "investing_cash_flow": "i",
            "financing_cash_flow": "f",
            "fx_cash_effect": "x",
            "cash_begin": "b",
            "cash_end": "e",
        },
    }
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        == "ACCOUNTING_CERTIFIED"
    )
    shares = leaf(
        "s",
        "diluted_shares",
        "3",
        concept="us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
        unit="shares",
    )
    eps = leaf("p", "eps_diluted", "0.33", unit="USD/share")
    income = leaf("n", "income_available_to_common", "1")
    data, history = setup([shares, eps, income], [shares])
    evidence = {
        "identity_id": "DILUTED_SHARES_EPS",
        "metric": "diluted_shares",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "component_ids": {
            "diluted_shares": "s",
            "eps_diluted": "p",
            "income_available_to_common": "n",
        },
        "income_scope": "AVAILABLE_TO_COMMON",
        "rounding_evidence": {
            "source_ids": ["s", "p", "n"],
            "eps_decimal_places": 2,
            "shares_decimal_places": 0,
            "income_decimal_places": 0,
        },
    }
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        == "ACCOUNTING_CERTIFIED"
    )
    evidence.pop("rounding_evidence")
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        != "ACCOUNTING_CERTIFIED"
    )


def test_debt_nonoverlap_and_net_income_scope_are_explicit():
    current = leaf(
        "d1", "debt_component:DebtCurrent", "2", concept="us-gaap:DebtCurrent", start=None
    )
    noncurrent = leaf(
        "d2",
        "debt_component:LongTermDebtNoncurrent",
        "3",
        concept="us-gaap:LongTermDebtNoncurrent",
        start=None,
    )
    data, history = setup([current, noncurrent], [])
    data["policy"]["debt_components"] = ["DebtCurrent", "LongTermDebtNoncurrent"]
    history["observations"] = [
        {
            "metric": "total_debt",
            "value_decimal": "5",
            "unit": "USD",
            "currency": "USD",
            "period_start": None,
            "period_end": "2024-12-31",
            "frequency": "annual",
            "record_kind": "DERIVED",
            "formula": "SUM",
            "source_observation_ids": ["d1", "d2"],
            "source_terms": [
                {"source_observation_id": "d1", "coefficient": 1},
                {"source_observation_id": "d2", "coefficient": 1},
            ],
        }
    ]
    evidence = {
        "identity_id": "DEBT_COMPONENT_SUM",
        "metric": "total_debt",
        "period_start": None,
        "period_end": "2024-12-31",
        "component_ids": {"current": "d1", "noncurrent": "d2"},
        "nonoverlap_evidence": {
            "approved": True,
            "evidence_id": "reviewed-definition-v1",
            "source_ids": ["d1", "d2"],
        },
    }
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        == "ACCOUNTING_CERTIFIED"
    )
    evidence.pop("nonoverlap_evidence")
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        != "ACCOUNTING_CERTIFIED"
    )
    net = leaf("n", "net_income", "7", concept="us-gaap:NetIncomeLoss")
    data, history = setup(
        [net, leaf("p", "pretax_income", "10"), leaf("t", "tax_expense", "3")], [net]
    )
    evidence = {
        "identity_id": "PRETAX_MINUS_TAX_EQUALS_NET_INCOME",
        "metric": "net_income",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "component_ids": {"pretax_income": "p", "tax_expense": "t", "net_income": "n"},
        "scope_confirmed": True,
    }
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        == "ACCOUNTING_CERTIFIED"
    )
    evidence["scope_confirmed"] = False
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        != "ACCOUNTING_CERTIFIED"
    )


def test_wrong_source_definition_and_wrong_fcf_formula_are_not_verified():
    revenue = leaf("r", "revenue", "10", concept="us-gaap:UnrelatedRevenueTag")
    data, history = setup([revenue])
    assert certify(data, history)["observations"][0]["status"] == "UNAVAILABLE"
    ocf, capex = leaf("o", "operating_cash_flow", "10"), leaf("c", "capital_expenditures", "2")
    data, history = setup([ocf, capex], [])
    history["observations"] = [
        {
            "metric": "free_cash_flow",
            "value_decimal": "12",
            "unit": "USD",
            "currency": "USD",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "frequency": "annual",
            "record_kind": "DERIVED",
            "formula": "SUBTRACT",
            "source_observation_ids": ["o", "c"],
            "source_terms": [
                {"source_observation_id": "o", "coefficient": 1},
                {"source_observation_id": "c", "coefficient": 1},
            ],
        }
    ]
    assert certify(data, history)["observations"][0]["status"] == "UNAVAILABLE"


def test_cash_target_certification_and_no_role_substitution():
    ocf = leaf("o", "operating_cash_flow", "10")
    cash = leaf(
        "e", "cash", "25", concept="us-gaap:CashAndCashEquivalentsAtCarryingValue", start=None
    )
    rows = [
        ocf,
        cash,
        leaf("b", "cash", "20", start=None, end="2023-12-31"),
        leaf("i", "investing_cash_flow", "-4"),
        leaf("f", "financing_cash_flow", "-2"),
        leaf("x", "fx_cash_effect", "1"),
    ]
    data, history = setup(rows, [cash])
    evidence = {
        "identity_id": "CASH_MOVEMENT",
        "metric": "cash",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "component_ids": {
            "operating_cash_flow": "o",
            "investing_cash_flow": "i",
            "financing_cash_flow": "f",
            "fx_cash_effect": "x",
            "cash_begin": "b",
            "cash_end": "e",
        },
    }
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        == "ACCOUNTING_CERTIFIED"
    )
    rows[0]["metric"] = "unrelated_metric"
    assert (
        certify(data, history, accounting_inputs=[evidence])["observations"][0]["status"]
        != "ACCOUNTING_CERTIFIED"
    )


def test_accounting_identity_cannot_cherry_pick_conflicting_components():
    revenue = leaf("r", "revenue", "10")
    cost = leaf("c", "cost_of_revenue", "6")
    gross = leaf("g", "gross_profit", "4")
    conflicting = {**gross, "source_observation_id": "g-conflict", "value_decimal": "5"}
    data, history = setup([revenue, cost, gross, conflicting], [revenue])
    evidence = {
        "identity_id": "REVENUE_MINUS_COST_EQUALS_GROSS_PROFIT",
        "metric": "revenue",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "component_ids": {"revenue": "r", "cost_of_revenue": "c", "gross_profit": "g"},
    }
    row = certify(data, history, accounting_inputs=[evidence])["observations"][0]
    assert row["status"] != "ACCOUNTING_CERTIFIED"
    assert "CONFLICTING_ACCOUNTING_COMPONENT_ALTERNATIVES" in row["reasons"]
