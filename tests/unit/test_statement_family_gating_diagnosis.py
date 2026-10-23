"""Unit checks for the statement-family gating diagnosis (issuer x period).

The diagnosis must show, without changing any policy value, that statement-family
gating is issuer-global and therefore fail-closed in every observation period —
including periods after taxonomy evidence stops (GE's portfolio changes) — and
that the banking/insurance quarantine and the capex/FCF/debt restrictions stay
exactly as deployed. No test asserts a financial value beyond the fixtures.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

from diagnose_statement_family_gating import (  # noqa: E402
    expected_quarantine,
    ge_timeline,
    issuer_diagnosis,
    quarantine_preservation,
    universe_preservation,
    universe_scan,
)
from test_revenue_coverage_diagnosis import GE, WMT, WMT_TAGS, _company  # noqa: E402

AS_OF = "2026-09-24T00:00:00Z"
MEMBER = "a" * 64

STATEMENT_FAMILY = {
    "revenue",
    "operating_income",
    "capital_expenditures",
    "free_cash_flow",
    "total_debt",
}


def _ge_diagnosis():
    # Lending evidence exists only in FY2020; after the portfolio changes the
    # 2021 filings carry no lending tags at all.
    company = _company(
        GE,
        {"Revenues": [("2020-12-31", 79.0), ("2021-12-31", 76.0)]},
        instants=[("LoansAndLeasesReceivableNetReportedAmount", "2020-12-31", 1.2e9)],
    )
    return company, issuer_diagnosis(company, GE, MEMBER, date_to="2026-09-24", as_of=AS_OF)


def test_evidence_stopping_in_later_periods_does_not_release_any_period():
    _, diagnosis = _ge_diagnosis()
    assert diagnosis["family"] == "financial_statement_evidence"
    assert diagnosis["fail_closed_every_period"] is True
    by_period = {row["period_end"]: row for row in diagnosis["periods"]}
    assert by_period["2020-12-31"]["evidence_row_count"] == 1
    assert by_period["2021-12-31"]["evidence_row_count"] == 0
    for period in by_period.values():
        assert set(period["gating"]) == STATEMENT_FAMILY
        assert set(period["gated_out_in_period"]) == STATEMENT_FAMILY


def test_ge_timeline_labels_evidence_presence_and_gating_by_fiscal_year():
    _, diagnosis = _ge_diagnosis()
    timeline = ge_timeline(diagnosis)
    assert timeline["2020"]["lending_rows"] == 1
    assert timeline["2020"]["evidence_tags"] == ["LoansAndLeasesReceivableNetReportedAmount"]
    assert timeline["2021"]["lending_rows"] == 0
    assert timeline["2021"]["evidence_tags"] == []
    assert all(row["gating_in_effect"] for row in timeline.values())


def test_expected_quarantine_follows_the_deployed_policy_rule():
    assert expected_quarantine(GE, "financial_statement_evidence") == {
        metric: "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED"
        for metric in STATEMENT_FAMILY
    }
    # A debt-component issuer without overlap quarantine carries no total_debt gate.
    assert expected_quarantine("0001045810", "general_us_gaap_unclassified") == {}
    assert expected_quarantine("0000320193", "general_us_gaap_unclassified") == {
        "total_debt": "COMPONENT_NONOVERLAP_NOT_CONFIRMED"
    }
    assert expected_quarantine(WMT, "general_us_gaap_unclassified") == {
        "total_debt": "COMPONENT_NONOVERLAP_NOT_CONFIRMED"
    }


def _entry(family, quarantined, *, served=None, quarantined_entry=False):
    if quarantined_entry:
        return {
            "label": None,
            "quarantine": {
                "member_sha256": "b" * 64,
                "reason": "NO_VALID_MAPPED_FINANCIAL_HISTORY",
                "source_eligibility": {"classification": "NO_US_GAAP_TAXONOMY", "eligible": False},
            },
            "archive_entry": {"compressed_bytes": 268},
            "source_eligibility": {"classification": "NO_US_GAAP_TAXONOMY", "eligible": False},
        }
    coverage = {
        frequency: {
            metric: {"observations": (served or {}).get((frequency, metric), 0), "gaps": 1}
            for metric in STATEMENT_FAMILY
        }
        for frequency in ("annual", "quarterly", "ttm")
    }
    return {
        "label": "Fixture",
        "policy": {
            "family": family,
            "classification_evidence": ["PremiumsEarnedNet"]
            if family == "financial_statement_evidence"
            else [],
            "quarantined_metrics": quarantined,
            "revenue_concepts": ["Revenues"],
        },
        "coverage": coverage,
        "materialized": {"member_sha256": "a" * 64},
        "archive_entry": {"compressed_bytes": 1000},
    }


def test_universe_scan_classifies_every_entry_shape_and_flags_leaks():
    catalog = {
        "0000000001": _entry(
            "financial_statement_evidence",
            {metric: "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED" for metric in STATEMENT_FAMILY},
        ),
        "0000000002": _entry(
            "financial_statement_evidence",
            {metric: "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED" for metric in STATEMENT_FAMILY},
            served={("annual", "revenue"): 3},
        ),
        "0000000003": _entry(None, None, quarantined_entry=True),
    }
    scan = universe_scan(catalog)
    rows = {row["cik"]: row for row in scan["issuers"]}
    assert scan["totals"]["issuers"] == 3
    assert scan["totals"]["families"] == {
        "ENTRY_QUARANTINED": 1,
        "financial_statement_evidence": 2,
    }
    assert scan["totals"]["entry_quarantine_reasons"] == {
        "NO_VALID_MAPPED_FINANCIAL_HISTORY": 1
    }
    assert rows["0000000001"]["fail_closed"] is True
    assert rows["0000000002"]["fail_closed"] is False
    assert rows["0000000002"]["quarantine_matches_policy_rule"] is True
    assert rows["0000000003"]["fail_closed"] is True
    assert rows["0000000003"]["entry_quarantined"] is True
    assert scan["totals"]["fail_closed_every_issuer"] is False
    assert scan["totals"]["fail_closed_violations"] == [
        {"cik": "0000000002", "leaked": ["annual:revenue"]}
    ]


def test_universe_preservation_aggregates_policy_state():
    catalog = {
        "0000000001": _entry(
            "financial_statement_evidence",
            {metric: "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED" for metric in STATEMENT_FAMILY},
        ),
        "0000000003": _entry(None, None, quarantined_entry=True),
    }
    preserved = universe_preservation(universe_scan(catalog))
    assert preserved["issuers_checked"] == 1
    assert preserved["entry_quarantined_issuers"] == 1
    assert preserved["all_quarantines_match_policy_rule"] is True
    assert preserved["capex_fcf_debt_restrictions_released"] == []
    assert preserved["fail_closed_every_issuer"] is True


def test_quarantine_preservation_keeps_banking_insurance_and_debt_restrictions():
    company, ge = _ge_diagnosis()
    wmt = issuer_diagnosis(
        _company(WMT, WMT_TAGS), WMT, MEMBER, date_to="2026-09-24", as_of=AS_OF
    )
    preserved = quarantine_preservation({GE: ge, WMT: wmt})
    assert preserved["policy_unchanged_by_this_work"] is True
    assert set(preserved["issuers"][GE]["quarantined_metrics"]) == STATEMENT_FAMILY
    assert preserved["issuers"][GE]["capex_fcf_debt_restrictions_released"] == []
    assert preserved["issuers"][WMT]["quarantined_metrics"] == {
        "total_debt": "COMPONENT_NONOVERLAP_NOT_CONFIRMED"
    }
    assert "audited statements" in preserved["release_requires"]
    assert "not a repair" in preserved["release_requires"]
