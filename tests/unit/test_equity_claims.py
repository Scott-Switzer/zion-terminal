"""Claim-extraction semantics of the versioned Zion equity claims capability.

Every case is self-contained synthetic Company Facts data: no network, no archive.
The cases pin the policies the module documents, including the two that matter most
for the enterprise-to-equity bridge: a missing concept is never a zero claim, and a
redeemable minority subset is never subtracted twice.
"""

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))

from equity_claims import (  # noqa: E402
    AVAILABILITY_POLICY,
    MAX_AGE_DAYS,
    PREFERRED_SHARE_ROUNDING_TOLERANCE,
    VERSION,
    build_claims,
    cash_claim,
    common_shares_claim,
    debt_claim,
    eligible,
    end_of_day_utc,
    finance_lease_claim,
    noncontrolling_claim,
    nonoperating_asset_claim,
    operating_lease_claim,
    preferred_claim,
    restrict_to_available,
    selected,
)

ASOF = date(2026, 10, 14)
EARNINGS_END = "2026-06-30"
EARNINGS_FILED = "2026-07-23"
EARNINGS_ACCN = "0001652044-26-000071"


def row(val, end, filed, form="10-Q", accn="0000000000-26-000001", **extra):
    body = dict(val=val, end=end, filed=filed, form=form, accn=accn)
    body.update(extra)
    return body


def quarter(val, **extra):
    return row(val, EARNINGS_END, EARNINGS_FILED, form="10-Q", accn=EARNINGS_ACCN, **extra)


def companyfacts(us_gaap=None, dei=None):
    def body(mapping):
        return {tag: {"units": units} for tag, units in (mapping or {}).items()}

    return {"us-gaap": body(us_gaap), "dei": body(dei)}


def bridgeable_facts(cash=100):
    """Facts where every mandatory bridge claim is certified except NCI, which is reviewed absent."""
    return companyfacts(
        us_gaap={
            "CashAndCashEquivalentsAtCarryingValue": {"USD": [quarter(cash)]},
            "ShortTermInvestments": {"USD": [quarter(20)]},
            "DebtCurrent": {"USD": [quarter(1000)]},
            "LongTermDebtNoncurrent": {"USD": [quarter(32366)]},
            "FinanceLeaseLiability": {"USD": [quarter(66)]},
            "ConvertiblePreferredStockNonredeemableOrRedeemableIssuerOptionValue": {
                "USD": [quarter(18023000000)]
            },
            "PreferredStockSharesOutstanding": {"shares": [quarter(19000000)]},
            "CommonStockSharesOutstanding": {"shares": [quarter(12230000000)]},
            "StockholdersEquity": {"USD": [quarter(640480000000)]},
        }
    )


def decimal_of(value):
    return Decimal(str(value))


# --- point-in-time selection -------------------------------------------------


def test_valuation_date_cutoff_latest_period_and_latest_filing_tie_break():
    facts = companyfacts(
        us_gaap={
            "CashAndCashEquivalentsAtCarryingValue": {
                "USD": [
                    quarter(100),
                    row(999, "2026-09-30", "2026-10-30"),  # filed after the valuation date
                    row(50, "2026-03-31", "2026-09-01"),  # older period, later filing
                    row(105, "2026-06-30", "2026-08-01", accn="0001652044-26-000099"),
                ]
            }
        }
    )
    picked = selected(facts, "CashAndCashEquivalentsAtCarryingValue", ASOF)
    assert picked["value_decimal"] == "105"
    assert picked["period_end"] == EARNINGS_END
    assert picked["accession"] == "0001652044-26-000099"
    assert picked["available_at"] == "2026-08-01"
    assert picked["availability_granularity"] == "FILING_DATE_ONLY"


def test_stale_duration_and_dimensional_facts_are_not_eligible():
    floor = ASOF - timedelta(days=MAX_AGE_DAYS)
    facts = companyfacts(
        us_gaap={
            "CommercialPaper": {
                "USD": [
                    row(7, (floor - timedelta(days=1)).isoformat(), "2025-09-28", form="10-K"),
                    row(0, "2025-06-30", "2025-07-30", form="10-K"),
                ]
            },
            "LongTermDebtCurrent": {
                "USD": [
                    row(50, EARNINGS_END, EARNINGS_FILED, start="2025-07-01"),
                    row(60, EARNINGS_END, EARNINGS_FILED, segment={"dim": "x"}),
                ]
            },
        }
    )
    assert eligible(facts, "CommercialPaper", ASOF) == []
    assert selected(facts, "CommercialPaper", ASOF) is None
    assert eligible(facts, "LongTermDebtCurrent", ASOF) == []

    boundary = companyfacts(
        us_gaap={"CommercialPaper": {"USD": [row(1, floor.isoformat(), "2025-09-28", form="10-K")]}}
    )
    assert selected(boundary, "CommercialPaper", ASOF)["value_decimal"] == "1"


# --- debt -------------------------------------------------------------------


def test_filer_stated_current_debt_total_is_not_summed_with_its_own_component():
    facts = companyfacts(
        us_gaap={
            "DebtCurrent": {"USD": [row(1000000000, "2026-07-26", "2026-08-26")]},
            "LongTermDebtCurrent": {"USD": [row(1000000000, "2026-07-26", "2026-08-26")]},
            "LongTermDebtNoncurrent": {"USD": [row(32366000000, "2026-07-26", "2026-08-26")]},
        }
    )
    claim = debt_claim(facts, ASOF)
    assert claim["evidence_state"] == "REPORTED"
    assert claim["value_decimal"] == "33366000000"
    assert claim["notes"] == []
    assert {s["concept"] for s in claim["sources"]} == {
        "us-gaap:DebtCurrent",
        "us-gaap:LongTermDebtNoncurrent",
    }
    assert claim["sources"][0]["period_end"] == "2026-07-26"


def test_current_portion_plus_borrowings_is_used_when_no_current_total_is_reported():
    facts = companyfacts(
        us_gaap={
            "LongTermDebtCurrent": {"USD": [row(11007000000, "2026-06-27", "2026-07-31")]},
            "CommercialPaper": {"USD": [row(1997000000, "2026-06-27", "2026-07-31")]},
            "LongTermDebtNoncurrent": {"USD": [row(71340000000, "2026-06-27", "2026-07-31")]},
        }
    )
    claim = debt_claim(facts, ASOF)
    # 11007000000 + 1997000000 current plus 71340000000 noncurrent
    assert claim["value_decimal"] == "84344000000"
    assert {s["concept"] for s in claim["sources"]} == {
        "us-gaap:CommercialPaper",
        "us-gaap:LongTermDebtCurrent",
        "us-gaap:LongTermDebtNoncurrent",
    }
    assert claim["notes"] == []


def test_combined_total_at_another_period_is_ignored_and_same_period_gap_is_recorded():
    other_period = companyfacts(
        us_gaap={
            "LongTermDebtCurrent": {"USD": [row(11007000000, "2026-06-27", "2026-07-31")]},
            "LongTermDebtNoncurrent": {"USD": [row(71340000000, "2026-06-27", "2026-07-31")]},
            "LongTermDebt": {"USD": [row(82300000000, "2025-12-31", "2026-02-05", form="10-K")]},
        }
    )
    claim = debt_claim(other_period, ASOF)
    assert claim["value_decimal"] == "82347000000"
    assert any("different period than the components; the total is ignored." in n for n in claim["notes"])
    assert "us-gaap:LongTermDebt" not in {s["concept"] for s in claim["sources"]}

    same_period = companyfacts(
        us_gaap={
            "LongTermDebtCurrent": {"USD": [row(11007000000, "2026-06-27", "2026-07-31")]},
            "LongTermDebtNoncurrent": {"USD": [row(71340000000, "2026-06-27", "2026-07-31")]},
            "LongTermDebt": {"USD": [row(82300000000, "2026-06-27", "2026-07-31")]},
        }
    )
    claim = debt_claim(same_period, ASOF)
    assert claim["value_decimal"] == "82347000000"
    assert any("differs from the reported components by -47000000" in n for n in claim["notes"])
    assert any("components are used." in n for n in claim["notes"])


def test_combined_total_without_a_current_portion_or_total_is_compared_not_crashed():
    """A filer that files a combined long-term total and a noncurrent portion only.

    This combination raised IndexError inside the same-period comparison: the guard inspected
    the first key of an empty filer-stated current-debt total. Nothing here is contradictory,
    so the noncurrent component stays the claim and the disagreement is disclosed.
    """

    facts = companyfacts(
        us_gaap={
            "LongTermDebt": {"USD": [row(900, "2026-06-30", "2026-08-01", form="10-K")]},
            "LongTermDebtNoncurrent": {
                "USD": [row(500, "2026-06-30", "2026-08-01", form="10-K")]},
        }
    )
    claim = debt_claim(facts, ASOF)
    assert claim["evidence_state"] == "REPORTED"
    assert claim["value_decimal"] == "500"
    assert any("differs from its same-period component by 400" in n for n in claim["notes"])
    assert any("the component is used." in n for n in claim["notes"])


def test_combined_total_matching_its_same_period_component_says_so():
    facts = companyfacts(
        us_gaap={
            "LongTermDebt": {"USD": [row(500, "2026-06-30", "2026-08-01", form="10-K")]},
            "LongTermDebtNoncurrent": {
                "USD": [row(500, "2026-06-30", "2026-08-01", form="10-K")]},
        }
    )
    claim = debt_claim(facts, ASOF)
    assert claim["value_decimal"] == "500"
    assert any("matches its same-period component" in n for n in claim["notes"])


def test_combined_total_alongside_a_filer_total_is_not_a_component_comparison():
    facts = companyfacts(
        us_gaap={
            "DebtCurrent": {"USD": [row(100, "2026-06-30", "2026-08-01", form="10-K")]},
            "LongTermDebt": {"USD": [row(900, "2026-06-30", "2026-08-01", form="10-K")]},
            "LongTermDebtNoncurrent": {
                "USD": [row(500, "2026-06-30", "2026-08-01", form="10-K")]},
        }
    )
    claim = debt_claim(facts, ASOF)
    assert claim["evidence_state"] == "REPORTED"
    assert claim["value_decimal"] == "600"
    assert any("different scope; the total is not used as a component comparison." in n
               for n in claim["notes"])


def test_component_larger_than_the_reported_current_total_is_conflicting():
    facts = companyfacts(
        us_gaap={
            "DebtCurrent": {"USD": [quarter(100)]},
            "ShortTermBorrowings": {"USD": [quarter(500)]},
            "LongTermDebtNoncurrent": {"USD": [quarter(400)]},
        }
    )
    claim = debt_claim(facts, ASOF)
    assert claim["evidence_state"] == "CONFLICTING"
    assert claim["value_decimal"] is None
    assert claim["permitted_for_valuation"] is False
    assert any("Potential duplicate: ShortTermBorrowings" in n for n in claim["notes"])
    assert any("A reported component exceeds the filer-stated current-debt total." in n for n in claim["notes"])
    assert [c["concept"] for c in claim["candidates"]] == ["us-gaap:ShortTermBorrowings"]


# --- leases -----------------------------------------------------------------


def test_finance_lease_total_is_never_added_to_its_own_parts_and_operating_leases_are_separate():
    both = companyfacts(
        us_gaap={
            "FinanceLeaseLiability": {"USD": [quarter(2590000000)]},
            "FinanceLeaseLiabilityCurrent": {"USD": [quarter(449000000)]},
            "FinanceLeaseLiabilityNoncurrent": {"USD": [quarter(2141000000)]},
        }
    )
    claim = finance_lease_claim(both, ASOF)
    assert claim["value_decimal"] == "2590000000"
    assert [s["concept"] for s in claim["sources"]] == ["us-gaap:FinanceLeaseLiability"]

    parts_only = companyfacts(
        us_gaap={
            "FinanceLeaseLiabilityCurrent": {"USD": [quarter(2)]},
            "FinanceLeaseLiabilityNoncurrent": {"USD": [quarter(3)]},
        }
    )
    assert finance_lease_claim(parts_only, ASOF)["value_decimal"] == "5"

    operating = companyfacts(
        us_gaap={
            "OperatingLeaseLiability": {"USD": [quarter(18037000000)]},
            "OperatingLeaseLiabilityCurrent": {"USD": [quarter(3446000000)]},
            "OperatingLeaseLiabilityNoncurrent": {"USD": [quarter(14591000000)]},
        }
    )
    claim = operating_lease_claim(operating, ASOF)
    assert claim["value_decimal"] == "18037000000"
    assert claim["included_in_enterprise_bridge"] is False
    assert any("operating lease cost is already inside EBIT" in n for n in claim["notes"])

    assert finance_lease_claim(companyfacts(), ASOF)["evidence_state"] == "REVIEWED_ABSENT"
    assert operating_lease_claim(companyfacts(), ASOF)["evidence_state"] == "REVIEWED_ABSENT"


# --- nonoperating assets ----------------------------------------------------


def test_nonoperating_assets_include_marketable_securities_once_and_exclude_long_term_investments():
    facts = companyfacts(
        us_gaap={
            "CashAndCashEquivalentsAtCarryingValue": {"USD": [quarter(10)]},
            "ShortTermInvestments": {"USD": [quarter(20)]},
            "MarketableSecuritiesCurrent": {"USD": [quarter(30)]},
            "MarketableSecuritiesNoncurrent": {"USD": [quarter(40)]},
            "OtherLongTermInvestments": {"USD": [quarter(99)]},
        }
    )
    claim = nonoperating_asset_claim(facts, ASOF)
    # 10 cash + MarketableSecuritiesCurrent 30 (the alphabetically first current alternative) + 40 noncurrent
    assert claim["value_decimal"] == "80"
    assert "us-gaap:ShortTermInvestments" not in {s["concept"] for s in claim["sources"]}
    assert any("not added, because it measures the same securities." in n for n in claim["notes"])
    assert any("Excluded by policy: us-gaap:OtherLongTermInvestments" in n for n in claim["notes"])
    assert claim["excluded_concepts"] == ["us-gaap:OtherLongTermInvestments"]
    assert cash_claim(facts, ASOF)["value_decimal"] == "10"


# --- noncontrolling interests ----------------------------------------------


def test_vie_disclosure_measure_is_a_reported_claim_not_an_absence_and_redeemable_is_not_double_counted():
    facts = companyfacts(
        us_gaap={"NoncontrollingInterestInVariableInterestEntity": {"USD": [quarter(7100000000)]}}
    )
    claim = noncontrolling_claim(facts, ASOF)
    assert claim["evidence_state"] == "REPORTED"
    assert claim["value_decimal"] == "7100000000"
    assert claim["classification"] == "VIE_DISCLOSURE_NONCONTROLLING_INTEREST"
    assert claim["treatment"] == "DEDUCT_FROM_CONSOLIDATED_ENTERPRISE_VALUE"
    assert claim["deduction_permitted"] is True
    assert claim["permitted_for_valuation"] is True
    assert any("No separate noncontrolling interest line is reported" in n for n in claim["notes"])
    assert claim["redeemable_subset"]["evidence_state"] == "UNAVAILABLE"
    assert claim["redeemable_subset"]["value_decimal"] is None
    assert "Deducting both would double count." in claim["redeemable_subset"]["note"]


def test_exposed_redeemable_subset_is_reported_separately_from_the_total():
    facts = companyfacts(
        us_gaap={
            "NoncontrollingInterestInVariableInterestEntity": {"USD": [quarter(7100000000)]},
            "RedeemableNoncontrollingInterestEquityCarryingAmount": {"USD": [quarter(824000000)]},
        }
    )
    claim = noncontrolling_claim(facts, ASOF)
    assert claim["value_decimal"] == "7100000000"
    assert claim["redeemable_subset"]["evidence_state"] == "REPORTED"
    assert claim["redeemable_subset"]["value_decimal"] == "824000000"
    assert claim["redeemable_subset"]["concept"] == (
        "us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount"
    )


def test_disagreeing_nci_measures_are_conflicting_and_block_the_deduction():
    facts = companyfacts(
        us_gaap={
            "MinorityInterest": {"USD": [quarter(5000000000)]},
            "NoncontrollingInterestInVariableInterestEntity": {"USD": [quarter(7100000000)]},
        }
    )
    claim = noncontrolling_claim(facts, ASOF)
    assert claim["evidence_state"] == "CONFLICTING"
    assert claim["deduction_permitted"] is False
    assert claim["permitted_for_valuation"] is False
    assert claim["conflicts"] and "disagree" in claim["conflicts"][0]
    assert {s["concept"] for s in claim["sources"]} == {
        "us-gaap:MinorityInterest",
        "us-gaap:NoncontrollingInterestInVariableInterestEntity",
    }

    agreeing = companyfacts(
        us_gaap={
            "MinorityInterest": {"USD": [quarter(7100000000)]},
            "NoncontrollingInterestInVariableInterestEntity": {"USD": [quarter(7100000000)]},
        }
    )
    assert noncontrolling_claim(agreeing, ASOF)["evidence_state"] == "REPORTED"
    assert noncontrolling_claim(agreeing, ASOF)["classification"] == "REPORTED_NONCONTROLLING_INTEREST"


def test_absent_noncontrolling_interest_is_reviewed_absent_not_a_zero_claim():
    claim = noncontrolling_claim(companyfacts(), ASOF)
    assert claim["evidence_state"] == "REVIEWED_ABSENT"
    assert claim["value_decimal"] is None
    assert claim["deduction_permitted"] is False
    assert claim["treatment"] == "NO_CLAIM_IDENTIFIED"
    assert any("A missing concept is not evidence of a zero claim." in n for n in claim["notes"])
    assert claim["sources"] == []


# --- preferred equity -------------------------------------------------------


def test_preferred_measures_are_distinct_and_the_convertible_carrying_concept_is_used():
    facts = companyfacts(
        us_gaap={
            "ConvertiblePreferredStockNonredeemableOrRedeemableIssuerOptionValue": {
                "USD": [quarter(18023000000)]
            },
            "PreferredStockSharesOutstanding": {"shares": [quarter(19000000)]},
            "PreferredStockLiquidationPreference": {"USD/shares": [quarter(1000)]},
            "PreferredStockDividendRatePercentage": {"pure": [quarter(0.0625)]},
            "DividendsPreferredStockCash": {"USD": [quarter(86000000)]},
            "IncrementalCommonSharesAttributableToConversionOfPreferredStock": {
                "shares": [quarter(8000000)]
            },
        }
    )
    claim = preferred_claim(facts, ASOF)
    assert claim["evidence_state"] == "REPORTED"
    assert claim["carrying_concept"] == (
        "us-gaap:ConvertiblePreferredStockNonredeemableOrRedeemableIssuerOptionValue"
    )
    assert claim["value_decimal"] == "18023000000"
    assert claim["carrying_value_decimal"] == "18023000000"
    assert claim["shares_outstanding_decimal"] == "19000000"
    assert claim["liquidation_preference_per_share_decimal"] == "1000"
    assert claim["liquidation_preference_per_share_unit"] == "USD/shares"
    assert claim["liquidation_preference_total_decimal"] is None  # never invented from the per-share value
    assert claim["dividend_rate_decimal"] == "0.0625"
    assert claim["dividend_cash_decimal"] == "86000000"
    assert claim["incremental_converted_common_shares_decimal"] == "8000000"
    assert any("period weighted" in n for n in claim["notes"])
    assert claim["unit"] == "USD"


def half_series_terms():
    return {
        "series": [
            {
                "name": "A",
                "depositary_shares": 192500000,
                "depositary_per_preferred": 20,
                "liquidation_preference_per_share": 1000,
                "min_rate_per_preferred": 2.2520,
                "max_rate_per_preferred": 2.8160,
            },
            {
                "name": "B",
                "depositary_shares": 192500000,
                "depositary_per_preferred": 20,
                "liquidation_preference_per_share": 1000,
                "min_rate_per_preferred": 2.2740,
                "max_rate_per_preferred": 2.8420,
            },
        ]
    }


def test_caller_supplied_terms_are_assumed_and_within_rounding_tolerance():
    facts = companyfacts(
        us_gaap={
            "ConvertiblePreferredStockNonredeemableOrRedeemableIssuerOptionValue": {
                "USD": [quarter(18023000000)]
            },
            "PreferredStockSharesOutstanding": {"shares": [quarter(19000000)]},
            "PreferredStockLiquidationPreference": {"USD/shares": [quarter(1000)]},
        }
    )
    terms = half_series_terms()
    claim = preferred_claim(facts, ASOF, terms=terms)
    dilution = claim["dilution_terms"]
    assert dilution["evidence_state"] == "ASSUMED"
    assert dilution["basis"] == "EXPLICIT_SELECTION_FROM_OFFERING_DOCUMENT;NOT_XBRL_VERIFIED"
    assert dilution["permitted_for_valuation"] is True
    assert claim["evidence_state"] == "DERIVED_VERIFIED"
    assert claim["permitted_for_valuation"] is True
    assert decimal_of(dilution["preferred_shares_decimal"]) == Decimal(19250000)
    assert decimal_of(dilution["liquidation_preference_total_decimal"]) == Decimal(19250000000)
    assert decimal_of(dilution["min_converted_common_shares_decimal"]) == (
        Decimal(9625000) * Decimal("2.2520") + Decimal(9625000) * Decimal("2.2740")
    )
    assert decimal_of(dilution["max_converted_common_shares_decimal"]) == (
        Decimal(9625000) * Decimal("2.8160") + Decimal(9625000) * Decimal("2.8420")
    )
    # The filing-sourced share count is rounded to millions; the difference stays inside tolerance.
    assert abs(Decimal(19250000) - Decimal(19000000)) <= PREFERRED_SHARE_ROUNDING_TOLERANCE
    assert claim["shares_outstanding_decimal"] == "19000000"


def test_terms_beyond_the_rounding_tolerance_conflict_and_fail_closed():
    facts = companyfacts(
        us_gaap={
            "ConvertiblePreferredStockNonredeemableOrRedeemableIssuerOptionValue": {
                "USD": [quarter(18023000000)]
            },
            "PreferredStockSharesOutstanding": {"shares": [quarter(12000000)]},
        }
    )
    claim = preferred_claim(facts, ASOF, terms=half_series_terms())
    assert claim["evidence_state"] == "CONFLICTING"
    assert claim["permitted_for_valuation"] is False
    assert claim["dilution_terms"]["evidence_state"] == "CONFLICTING"
    assert any("preferred shares but 12000000 are reported." in n for n in claim["conflicts"])
    assert not claim["dilution_terms"]["permitted_for_valuation"]


def test_malformed_offering_terms_are_rejected():
    facts = companyfacts(us_gaap={"PreferredStockSharesOutstanding": {"shares": [quarter(19000000)]}})
    with pytest.raises(ValueError, match="INVALID_PREFERRED_TERMS"):
        preferred_claim(facts, ASOF, terms={"series": []})
    with pytest.raises(ValueError, match="INVALID_PREFERRED_TERMS"):
        preferred_claim(facts, ASOF, terms={"series": [{"depositary_shares": 1}]})
    with pytest.raises(ValueError, match="INVALID_PREFERRED_TERMS"):
        preferred_claim(
            facts,
            ASOF,
            terms={"series": [{**half_series_terms()["series"][0], "depositary_per_preferred": 0}]},
        )
    assert preferred_claim(companyfacts(), ASOF)["evidence_state"] == "REVIEWED_ABSENT"


# --- common shares ----------------------------------------------------------


def test_cover_page_count_wins_and_dated_observations_are_newest_first():
    facts = companyfacts(
        us_gaap={"CommonStockSharesOutstanding": {"shares": [quarter(12230000000)]}},
        dei={
            "EntityCommonStockSharesOutstanding": {
                "shares": [
                    row(12230000000, EARNINGS_END, EARNINGS_FILED, accn=EARNINGS_ACCN),
                    row(12110000000, "2026-03-31", "2026-04-30"),
                    row(12240000000, EARNINGS_END, "2026-08-01", accn="0001652044-26-000099"),
                ]
            }
        },
    )
    claim = common_shares_claim(facts, ASOF)
    assert claim["evidence_state"] == "REPORTED"
    assert claim["basis"] == "COVER_PAGE_DEI"
    assert claim["value_decimal"] == "12240000000"
    assert claim["not_a_weighted_average"] is True
    assert claim["classes"]["evidence_state"] == "UNAVAILABLE"
    ends = [o["period_end"] for o in claim["dated_observations"]]
    assert ends == [EARNINGS_END, EARNINGS_END, "2026-03-31"]
    assert claim["dated_observations"][0]["available_at"] == "2026-08-01"
    assert claim["dated_observations"][-1]["available_at"] == "2026-04-30"
    assert all(len(o["evidence_digest"]) == 64 for o in claim["dated_observations"])


def test_balance_sheet_all_class_count_is_used_only_without_a_cover_page_count():
    facts = companyfacts(
        us_gaap={"CommonStockSharesOutstanding": {"shares": [quarter(12230000000)]}}
    )
    claim = common_shares_claim(facts, ASOF)
    assert claim["basis"] == "BALANCE_SHEET_ALL_CLASSES"
    assert claim["value_decimal"] == "12230000000"
    assert any("No cover-page count is available" in n for n in claim["notes"])
    assert any("class detail requires the filing cover page" in n for n in claim["notes"])

    missing = common_shares_claim(companyfacts(), ASOF)
    assert missing["evidence_state"] == "UNAVAILABLE"
    assert missing["value_decimal"] is None
    assert missing["dated_observations"] == []


# --- contract level ---------------------------------------------------------


def test_build_claims_carries_evidence_and_a_reconciled_aggregate():
    claims = build_claims(bridgeable_facts(), ASOF, cik="0001652044")
    assert claims["schema_version"] == VERSION
    assert claims["rights"] == "PUBLIC_SEC_ONLY"
    assert claims["cik"] == "0001652044"
    assert claims["valuation_date"] == ASOF.isoformat()
    for name, value in claims["claims"].items():
        assert len(value["evidence_digest"]) == 64, name
        assert value["evidence_state"] in claims["evidence_states"], name
    aggregate = claims["aggregate_claims"]
    assert aggregate["status"] == "AVAILABLE"
    assert set(aggregate["deduct_components"]) == {"debt", "finance_leases", "preferred_equity"}
    assert set(aggregate["add_components"]) == {"cash", "nonoperating_assets"}
    assert Decimal(aggregate["deduct_claims_decimal"]) == sum(
        Decimal(v) for v in aggregate["deduct_components"].values()
    )
    assert Decimal(aggregate["add_claims_decimal"]) == sum(
        Decimal(v) for v in aggregate["add_components"].values()
    )
    assert Decimal(aggregate["deduct_components"]["debt"]) == Decimal(33366)
    assert Decimal(aggregate["deduct_components"]["finance_leases"]) == Decimal(66)
    assert Decimal(aggregate["add_components"]["nonoperating_assets"]) == Decimal(120)
    assert claims["unresolved"] == []
    assert claims["certification"].startswith("FILING_BACKED_CLAIMS")


def test_a_changed_reported_token_changes_only_that_claim_evidence_digest():
    first = build_claims(bridgeable_facts(cash=100), ASOF)
    second = build_claims(bridgeable_facts(cash=101), ASOF)
    assert first["claims"]["cash"]["value_decimal"] == "100"
    assert second["claims"]["cash"]["value_decimal"] == "101"
    assert first["claims"]["cash"]["evidence_digest"] != second["claims"]["cash"]["evidence_digest"]
    assert (
        first["claims"]["debt"]["evidence_digest"] == second["claims"]["debt"]["evidence_digest"]
    )


def test_aggregate_is_unavailable_when_a_mandatory_claim_is_conflicting():
    facts = bridgeable_facts()
    # A same-period component larger than the filer's current-debt total cannot be inside it.
    facts["us-gaap"]["ShortTermBorrowings"] = {"units": {"USD": [quarter(5000)]}}
    claims = build_claims(facts, ASOF)
    assert claims["claims"]["debt"]["evidence_state"] == "CONFLICTING"
    assert claims["aggregate_claims"]["status"] == "UNAVAILABLE"
    assert claims["aggregate_claims"]["missing"] == ["debt"]
    assert claims["aggregate_claims"]["note"] == "A missing claim is never treated as zero in the bridge."
    reasons = {entry["reason"] for entry in claims["unresolved"] if entry["claim"] == "debt"}
    assert reasons == {"CLAIM_NOT_CERTIFIED_FOR_BRIDGE", "CLAIM_CONFLICTS_MUST_BE_RESOLVED"}
    assert {entry["claim"] for entry in claims["unresolved"]} == {"debt"}


def test_a_filed_zero_carrying_amount_is_reported_rather_than_reviewed_absent():
    # A filer that files a zero preferred carrying amount has a reported zero, which is evidence,
    # not an absence: it must not be collapsed into REVIEWED_ABSENT or dropped from the aggregate.
    facts = companyfacts(
        us_gaap={
            "PreferredStockValueOutstanding": {"USD": [quarter(0)]},
            "CashAndCashEquivalentsAtCarryingValue": {"USD": [quarter(10)]},
        }
    )
    claim = preferred_claim(facts, ASOF)
    assert claim["evidence_state"] == "REPORTED"
    assert claim["value_decimal"] == "0"
    assert claim["verified_absent"] is False
    assert claim["permitted_for_valuation"] is True


def test_reported_equity_total_is_reconciliation_evidence_not_a_claim():
    claims = build_claims(bridgeable_facts(), ASOF)
    totals = claims["reconciliation"]["reported_equity_totals"]
    assert [t["concept"] for t in totals] == ["us-gaap:StockholdersEquity"]
    assert totals[0]["value_decimal"] == "640480000000"
    assert totals[0]["accession"] == EARNINGS_ACCN
    assert len(totals[0]["evidence_digest"]) == 64
    assert "StockholdersEquity" not in claims["claims"]
    assert claims["reconciliation"]["double_count_guards"]
    assert claims["reconciliation"]["separately_classified"]["operating_leases"]
    assert claims["limits"]["class_level_share_counts_available"] is False


def test_availability_filter_keeps_only_rows_whose_filing_day_had_ended():
    facts = companyfacts(
        us_gaap={
            "CashAndCashEquivalentsAtCarryingValue": {
                "USD": [quarter(10), row(11, "2026-06-30", "2026-10-14", accn="late")]
            },
            "MarketableSecuritiesCurrent": {"USD": [row(5, "2026-06-30", None)]},
        },
        dei={"EntityCommonStockSharesOutstanding": {"shares": [quarter(9)]}},
    )
    def rows(mapping, tag):
        return mapping["us-gaap"][tag]["units"]["USD"]

    intraday = restrict_to_available(facts, end_of_day_utc(date(2026, 10, 14)) - 1)
    assert [r["val"] for r in rows(intraday, "CashAndCashEquivalentsAtCarryingValue")] == [10]
    # A row with no usable filing date can never be proven available.
    assert rows(intraday, "MarketableSecuritiesCurrent") == []
    assert intraday["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"][0]["val"] == 9
    end_of_day = restrict_to_available(facts, end_of_day_utc(date(2026, 10, 14)))
    assert [r["val"] for r in rows(end_of_day, "CashAndCashEquivalentsAtCarryingValue")] == [10, 11]
    # The filter never mutates the source mapping.
    assert len(rows(facts, "CashAndCashEquivalentsAtCarryingValue")) == 2


def test_availability_filter_rejects_an_unsupported_policy_or_cutoff():
    with pytest.raises(ValueError, match="UNSUPPORTED_AVAILABILITY_POLICY"):
        restrict_to_available(companyfacts(), 0, policy="WHATEVER")
    with pytest.raises(ValueError, match="INVALID_AVAILABILITY_CUTOFF"):
        restrict_to_available(companyfacts(), "not-an-integer")


def test_a_filtered_out_fact_is_classified_rather_than_failing_the_contract():
    facts = bridgeable_facts()
    late = facts["us-gaap"]["CashAndCashEquivalentsAtCarryingValue"]["units"]["USD"]
    late[0] = dict(late[0], filed="2026-10-14")
    claims = build_claims(
        restrict_to_available(facts, end_of_day_utc(date(2026, 10, 13))), ASOF
    )
    assert claims["claims"]["cash"]["evidence_state"] == "REVIEWED_ABSENT"
    assert claims["claims"]["cash"]["value_decimal"] is None
    assert claims["aggregate_claims"]["status"] == "AVAILABLE"
    assert "cash" in claims["aggregate_claims"]["verified_absent"]


def test_every_add_claim_absent_at_once_is_a_verified_zero_not_an_error():
    facts = companyfacts(
        us_gaap={
            "LongTermDebtCurrent": {"USD": [quarter(1)]},
            "StockholdersEquity": {"USD": [quarter(640480000000)]},
        }
    )
    claims = build_claims(facts, ASOF)
    assert claims["aggregate_claims"]["status"] == "AVAILABLE"
    assert claims["aggregate_claims"]["add_claims_decimal"] == "0"
    assert claims["aggregate_claims"]["add_components"] == {}
    assert {"cash", "nonoperating_assets"} <= set(claims["aggregate_claims"]["verified_absent"])


def test_availability_policy_is_part_of_the_published_contract_limits():
    claims = build_claims(bridgeable_facts(), ASOF)
    assert claims["limits"]["availability_policy"] == AVAILABILITY_POLICY
    assert "filing date" in claims["limits"]["availability_rule"]
