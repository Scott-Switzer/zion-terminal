"""Diagnose the six issuers with zero stored revenue using representative fixtures.

The fixtures reproduce the concept/period shapes observed for the real CIKs on
2026-10-22 (SEC companyconcept API) and the frozen engine release
``b39a670b...``. They encode values needed to distinguish three separate causes:

1. definitional collision (Walmart files both total ``Revenues`` and ASC-606
   ``RevenueFromContractWithCustomerExcludingAssessedTax`` with different values
   for the same fiscal year) — now resolved for Walmart only, by the issuer
   pin ``REVENUE_CONCEPTS["0000104169"] = ["Revenues"]`` in both revenue
   registries (``sec_engine.py`` and ``sec_history.py``); every other issuer
   keeps the collision rule;
2. a registry omission for an issuer whose only revenue concept is not in the
   engine registry (Goldman Sachs files ``RevenuesNetOfInterestExpense``);
3. the statement-family policy, which quarantines revenue for any issuer whose
   taxonomy shows insurance or lending evidence.

No test asserts a financial value beyond the fixture's own numbers.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
import sec_engine
from sec_engine import history, normalize_sources, statement_policy

sys.path.insert(0, str(Path(__file__).parent))
from test_sec_history import fact, source

RELEASE = "b" * 64
AS_OF = "2026-10-22T00:00:00Z"

WMT = "0000104169"
GS = "0000886982"
ALL = "0000899051"
GE = "0000040545"
C = "0000831001"
JPM = "0000019617"


def _fiscal_year(rows):
    """Rows expressed as (fiscal_end, value_in_billions) -> company facts rows."""
    out = []
    for index, (end, value) in enumerate(rows):
        end_date = date.fromisoformat(end)
        # Walmart's fiscal year ends on 31 January, so it starts the previous February.
        start = (
            date(end_date.year - 1, 2, 1)
            if (end_date.month, end_date.day) == (1, 31)
            else date(end_date.year, 1, 1)
        )
        out.append(
            {
                "start": start.isoformat(),
                "end": end,
                "val": int(round(value * 1_000_000_000)),
                "filed": (end_date + timedelta(days=45)).isoformat(),
                "accn": f"accn-{end}-{index}",
                "form": "10-K",
                "fy": end_date.year,
                "fp": "FY",
            }
        )
    return out


def _company(cik, tags, instants=None):
    company = source([])
    company["cik"] = int(cik)
    company["facts"] = {"us-gaap": {}}
    for tag, rows in tags.items():
        company["facts"]["us-gaap"][tag] = {"units": {"USD": _fiscal_year(rows)}}
    for tag, end, value in instants or []:
        company["facts"]["us-gaap"][tag] = {
            "units": {"USD": [{"end": end, "val": int(value), "filed": end, "accn": "i-" + tag}]}
        }
    return company


def _revenue(cik, company, *, date_from="2007-01-01", date_to="2027-01-01"):
    data = normalize_sources(company, cik, RELEASE)
    result = history(
        data,
        metrics=["revenue"],
        frequency="annual",
        date_from=date_from,
        date_to=date_to,
        as_of=AS_OF,
    )
    return data, result


WMT_TAGS = {
    "Revenues": [("2024-01-31", 648.125), ("2025-01-31", 680.985), ("2026-01-31", 713.163)],
    "RevenueFromContractWithCustomerExcludingAssessedTax": [
        ("2024-01-31", 642.637),
        ("2025-01-31", 674.538),
        ("2026-01-31", 706.413),
    ],
}


def test_walmart_issuer_pin_selects_total_revenues_without_touching_values():
    # The pin is part of both registries now, so no test patching is involved.
    data, result = _revenue(WMT, _company(WMT, WMT_TAGS))
    assert [row["value_decimal"] for row in result["observations"]] == [
        "648125000000",
        "680985000000",
        "713163000000",
    ]
    assert {row["concept"] for row in result["observations"]} == {"us-gaap:Revenues"}
    assert all(row["frequency"] == "annual" for row in result["observations"])
    assert statement_policy(_company(WMT, WMT_TAGS))["revenue_concepts"] == ["Revenues"]
    assert data["policy"]["revenue_concepts"] == ["Revenues"]
    assert "revenue" not in data["policy"]["quarantined_metrics"]


def test_ambiguity_rule_is_preserved_for_every_unpinned_issuer():
    # The same collision shape on an unpinned issuer stays ambiguous: the pin
    # changes Walmart's definition choice only, never the collision rule.
    other = "0000999999"
    data, result = _revenue(other, _company(other, WMT_TAGS))
    assert result["observations"] == []
    reasons = {gap["reason"] for gap in result["coverage_gaps"]}
    assert "AMBIGUOUS_CONCEPT_OR_DERIVATION" in reasons
    assert data["policy"]["revenue_concepts"] == [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ]


def test_removing_the_walmart_pin_restores_the_collision(monkeypatch):
    monkeypatch.delitem(sec_engine.REVENUE_CONCEPTS, WMT)
    _, result = _revenue(WMT, _company(WMT, WMT_TAGS))
    assert result["observations"] == []
    reasons = {gap["reason"] for gap in result["coverage_gaps"]}
    assert "AMBIGUOUS_CONCEPT_OR_DERIVATION" in reasons


def test_revenue_concept_pins_are_identical_in_both_registries():
    import sec_history

    assert sec_engine.REVENUE_CONCEPTS == sec_history.REVENUE_CONCEPTS
    assert sec_engine.REVENUE_CONCEPTS == {"0000909832": ["Revenues"], WMT: ["Revenues"]}


def test_pin_serves_latest_restatement_but_keeps_every_revision(monkeypatch):
    monkeypatch.setitem(sec_engine.REVENUE_CONCEPTS, WMT, ["Revenues"])
    company = _company(WMT, {"Revenues": WMT_TAGS["Revenues"]})
    rows = company["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    # Walmart refiles a different FY2025 value in a later filing. The pin
    # selects the concept; revisions stay in the object and the latest filing
    # is the served value.
    rows.append(
        {
            **rows[1],
            "val": int(680.0 * 1_000_000_000),
            "accn": "accn-restated-2025",
            "filed": "2026-03-20",
            "fy": 2026,
            "fp": "FY",
        }
    )
    data, result = _revenue(WMT, company)
    served = {row["period_end"]: row["value_decimal"] for row in result["observations"]}
    assert served["2025-01-31"] == "680000000000"
    revisions = [
        row
        for row in data["observations"]
        if row["metric"] == "revenue" and row["period_end"] == "2025-01-31"
    ]
    assert len(revisions) == 2
    assert {row["value_decimal"] for row in revisions} == {"680985000000", "680000000000"}


def test_pin_refuses_conflicting_values_inside_one_filing(monkeypatch):
    monkeypatch.setitem(sec_engine.REVENUE_CONCEPTS, WMT, ["Revenues"])
    company = _company(WMT, {"Revenues": WMT_TAGS["Revenues"]})
    rows = company["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    rows.append(
        {
            **rows[1],
            "val": int(680.0 * 1_000_000_000),
            "accn": "accn-conflict-2025",
        }
    )
    _, result = _revenue(WMT, company)
    served = {row["period_end"]: row["value_decimal"] for row in result["observations"]}
    assert "2025-01-31" not in served
    assert any(
        gap["period_end"] == "2025-01-31" and gap["reason"] == "CONFLICTING_SOURCE_VALUES"
        for gap in result["coverage_gaps"]
    )


def test_goldman_revenue_concept_is_absent_from_the_registry_entirely():
    company = _company(
        GS,
        {"RevenuesNetOfInterestExpense": [("2025-12-31", 53.6), ("2026-12-31", 56.1)]},
        instants=[("LoansAndLeasesReceivableNetReportedAmount", "2026-12-31", 400e9)],
    )
    _, result = _revenue(GS, company)
    assert result["observations"] == []
    reasons = {gap["reason"] for gap in result["coverage_gaps"]}
    assert "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED" in reasons
    assert "NO_VALID_OBSERVATIONS_IN_REQUEST" in reasons


def test_goldman_concept_addition_alone_cannot_lift_the_statement_policy(monkeypatch):
    company = _company(
        GS,
        {"RevenuesNetOfInterestExpense": [("2025-12-31", 53.6), ("2026-12-31", 56.1)]},
        instants=[("LoansAndLeasesReceivableNetReportedAmount", "2026-12-31", 400e9)],
    )
    monkeypatch.setitem(
        sec_engine.REGISTRY["revenue"],
        "concepts",
        [*sec_engine.REGISTRY["revenue"]["concepts"], "RevenuesNetOfInterestExpense"],
    )
    data, result = _revenue(GS, company)
    normalized = [row for row in data["observations"] if row["metric"] == "revenue"]
    assert [row["value_decimal"] for row in normalized] == ["53600000000", "56100000000"]
    assert result["observations"] == []
    assert any(
        gap["reason"] == "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED"
        for gap in result["coverage_gaps"]
    )


@pytest.mark.parametrize(
    ("cik", "tags", "instants"),
    [
        (
            ALL,
            {
                "Revenues": [("2025-12-31", 67.69)],
                "PremiumsEarnedNet": [("2025-12-31", 55.2)],
            },
            None,
        ),
        (
            GE,
            {"Revenues": [("2025-12-31", 45.85)]},
            [("LoansAndLeasesReceivableNetReportedAmount", "2025-12-31", 1.2e9)],
        ),
        (
            C,
            {"Revenues": [("2025-12-31", 85.22)]},
            [("InterestAndDividendIncomeOperating", "2025-12-31", 60e9)],
        ),
        (
            JPM,
            {
                "Revenues": [("2025-12-31", 182.45)],
                "RevenuesNetOfInterestExpense": [("2025-12-31", 180.6)],
            },
            [("InterestAndDividendIncomeOperating", "2025-12-31", 95e9)],
        ),
    ],
)
def test_statement_family_evidence_quarantines_revenue_even_when_values_exist(
    cik, tags, instants
):
    company = _company(cik, tags, instants=instants)
    policy = statement_policy(company)
    assert policy["family"] == "financial_statement_evidence"
    assert policy["quarantined_metrics"]["revenue"] == (
        "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED"
    )
    _, result = _revenue(cik, company)
    assert result["observations"] == []
    assert any(
        gap["reason"] == "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED"
        for gap in result["coverage_gaps"]
    )


def test_quarantine_is_scoped_to_statement_metrics_not_to_balance_sheet_facts():
    company = _company(
        C,
        {"Revenues": [("2025-12-31", 85.22)]},
        instants=[("InterestAndDividendIncomeOperating", "2025-12-31", 60e9)],
    )
    quarantined = statement_policy(company)["quarantined_metrics"]
    assert set(quarantined) == {
        "revenue",
        "operating_income",
        "capital_expenditures",
        "free_cash_flow",
        "total_debt",
    }
    assert "net_income" not in quarantined
    assert "cash" not in quarantined


def test_walmart_collision_without_the_issuer_pin_stays_ambiguous(monkeypatch):
    # The pin is what resolves Walmart; the engine's ambiguity rule still fires
    # for any collision it does not recognise.
    monkeypatch.delitem(sec_engine.REVENUE_CONCEPTS, WMT, raising=False)
    data, result = _revenue(WMT, _company(WMT, WMT_TAGS))
    assert result["observations"] == []
    reasons = {gap["reason"] for gap in result["coverage_gaps"]}
    assert "AMBIGUOUS_CONCEPT_OR_DERIVATION" in reasons
    policy = statement_policy(_company(WMT, WMT_TAGS))
    assert policy["family"] == "general_us_gaap_unclassified"
    assert "revenue" not in policy["quarantined_metrics"]
    assert data["policy"]["quarantined_metrics"].get("total_debt") == (
        "COMPONENT_NONOVERLAP_NOT_CONFIRMED"
    )


def test_revenue_pins_agree_in_both_registries():
    import sec_history

    assert sec_engine.REVENUE_CONCEPTS == sec_history.REVENUE_CONCEPTS
