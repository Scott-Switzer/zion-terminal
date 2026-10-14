"""Independent arithmetic and economic-definition guards; no production helper reuse."""

import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from valuation_engine import calculate

CUTOFF = "2025-04-01T00:00:00Z"


def evidence(token, **changes):
    row = {
        "value_decimal": str(token),
        "unit": "USD",
        "currency": "USD",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "frequency": "annual",
        "available_at": "2025-02-20T17:00:00Z",
        "source_ids": ["independent-fixture-source"],
        "quality": "INDEPENDENTLY_RECONCILED",
    }
    row.update(changes)
    return row


def rational(result):
    assert result["quality"] == "DERIVATION_VERIFIED", result
    return Fraction(int(result["value_numerator"]), int(result["value_denominator"]))


def market_cap(token="1000"):
    return evidence(token, valuation_at="2025-03-31T20:00:00Z")


def complete_ev():
    return {
        "market_cap": market_cap(),
        "total_debt": evidence("211.30", definition="NONOVERLAPPING_INTEREST_BEARING_DEBT"),
        "cash": evidence("73.20"),
        "preferred_equity": evidence("11.40"),
        "noncontrolling_interest": evidence("7.60"),
    }


def test_independent_exact_flow_formulas_and_nonterminating_ratio():
    revenue = Decimal("37.11")
    operating = Decimal("-2.07")
    result = calculate(
        "operating_margin",
        {"revenue": evidence(revenue), "operating_income": evidence(operating)},
        as_of=CUTOFF,
    )
    assert rational(result) == Fraction(operating) / Fraction(revenue)
    assert result["value_decimal"] is None
    inputs = {
        "operating_cash_flow": evidence("47.31"),
        "capital_expenditures": evidence("13.09", definition="CASH_PPE_PURCHASES_POSITIVE_OUTFLOW"),
    }
    assert rational(calculate("free_cash_flow", inputs, as_of=CUTOFF)) == Fraction(3422, 100)


def test_independent_ev_arithmetic_and_each_missing_component():
    rows = complete_ev()
    result = calculate("enterprise_value", rows, as_of=CUTOFF, industry="nonfinancial_verified")
    assert rational(result) == Fraction(115710, 100)
    for omitted in rows:
        result = calculate(
            "enterprise_value",
            {k: v for k, v in rows.items() if k != omitted},
            as_of=CUTOFF,
            industry="nonfinancial_verified",
        )
        assert result["quality"] == "UNAVAILABLE"
        assert result["value_numerator"] is None


def test_market_cap_positive_same_class_actual_shares_and_same_date():
    rows = {
        "price": evidence(
            "12.37",
            unit="USD/share",
            security_id="class-common-A",
            price_basis="RAW",
            adjustment_method="NONE",
            session_date="2025-03-31",
            valuation_at="2025-03-31T20:00:00Z",
        ),
        "shares_outstanding": evidence(
            "1234567",
            currency=None,
            unit="shares",
            period_end="2025-03-31",
            security_id="class-common-A",
            definition="COMMON_SHARES_OUTSTANDING",
        ),
    }
    kw = {"as_of": CUTOFF, "issuer_security_scope": "SINGLE_COMMON_CLASS_VERIFIED"}
    assert rational(calculate("market_cap", rows, **kw)) == Fraction(1237 * 1234567, 100)
    for change in [
        {"definition": "WEIGHTED_AVERAGE_DILUTED_SHARES"},
        {"security_id": "preferred-B"},
        {"period_end": "2025-03-30"},
        {"value_decimal": "0"},
    ]:
        bad = {**rows, "shares_outstanding": {**rows["shares_outstanding"], **change}}
        assert calculate("market_cap", bad, **kw)["quality"] == "UNAVAILABLE"


def test_financial_publication_cannot_follow_the_price_even_with_later_query_cutoff():
    rows = {
        "market_cap": market_cap(),
        "net_income": evidence(
            "40",
            definition="NET_INCOME_ATTRIBUTABLE_TO_COMMON",
            available_at="2025-04-01T00:00:00Z",
        ),
    }
    result = calculate("price_to_earnings", rows, as_of="2025-06-01T00:00:00Z")
    assert result["quality"] == "UNAVAILABLE"
    assert "FINANCIAL_INPUT_AFTER_PRICE" in result["reasons"]


def test_fcf_requires_matched_periods_and_positive_cash_ppe_outflow():
    rows = {
        "operating_cash_flow": evidence("45"),
        "capital_expenditures": evidence(
            "12",
            definition="CASH_PPE_PURCHASES_POSITIVE_OUTFLOW",
            period_end="2023-12-31",
        ),
    }
    assert calculate("free_cash_flow", rows, as_of=CUTOFF)["quality"] == "UNAVAILABLE"
    rows["capital_expenditures"].update(period_end="2024-12-31", value_decimal="-12")
    assert calculate("free_cash_flow", rows, as_of=CUTOFF)["quality"] == "UNAVAILABLE"


@pytest.mark.parametrize("industry", [None, "UNKNOWN", "general_us_gaap"])
def test_ev_requires_positive_source_backed_nonfinancial_classification(industry):
    result = calculate("enterprise_value", complete_ev(), as_of=CUTOFF, industry=industry)
    assert result["quality"] == "UNAVAILABLE"


def test_leverage_cannot_use_quarterly_ebitda():
    result = calculate(
        "leverage",
        {
            "total_debt": evidence("50", definition="NONOVERLAPPING_INTEREST_BEARING_DEBT"),
            "ebitda": evidence(
                "10",
                definition="REPORTED_RECONCILED_EBITDA",
                frequency="quarterly",
                period_start="2024-10-01",
            ),
        },
        as_of=CUTOFF,
        industry="nonfinancial_verified",
    )
    assert result["quality"] == "UNAVAILABLE"


@pytest.mark.parametrize("metric", ["free_cash_flow_margin", "free_cash_flow_yield"])
def test_fcf_ratios_require_verified_cashflow_definition(metric):
    rows = {
        "free_cash_flow": evidence("20", definition="UNKNOWN"),
        "revenue": evidence("100"),
        "market_cap": market_cap(),
    }
    assert calculate(metric, rows, as_of=CUTOFF)["quality"] == "UNAVAILABLE"


def test_annual_multiple_rejects_one_day_flow_relabelled_annual():
    rows = {
        "market_cap": market_cap(),
        "revenue": evidence(
            "100",
            period_start="2024-12-31",
            period_end="2024-12-31",
            frequency="annual",
        ),
    }
    assert calculate("price_to_sales", rows, as_of=CUTOFF)["quality"] == "UNAVAILABLE"


def test_negative_chained_market_cap_never_yields_verified_multiple():
    rows = {"market_cap": market_cap("-1000"), "revenue": evidence("100")}
    assert calculate("price_to_sales", rows, as_of=CUTOFF)["quality"] == "UNAVAILABLE"


def test_certifier_cannot_transplant_prior_year_sources_into_new_fcf_components():
    from financial_certification import certify_history

    leaves = []
    for role, concept, token in [
        ("operating_cash_flow", "NetCashProvidedByUsedInOperatingActivities", "100"),
        ("capital_expenditures", "PaymentsToAcquirePropertyPlantAndEquipment", "20"),
    ]:
        leaves.append(
            {
                "source_observation_id": role + "-2023",
                "metric": role,
                "concept": "us-gaap:" + concept,
                "value_decimal": token,
                "cik": "0000000001",
                "member_sha256": "a" * 64,
                "accession": "0000000001-24-000001",
                "form": "10-K",
                "available_at": "2024-02-01T00:00:00Z",
                "unit": "USD",
                "currency": "USD",
                "period_start": "2023-01-01",
                "period_end": "2023-12-31",
            }
        )
    row = {
        "metric": "free_cash_flow",
        "value_decimal": "80",
        "unit": "USD",
        "currency": "USD",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "frequency": "annual",
        "record_kind": "DERIVED",
        "formula": "SUBTRACT",
        "source_observation_ids": [leaf["source_observation_id"] for leaf in leaves],
        "source_terms": [
            {
                "source_observation_id": leaf["source_observation_id"],
                "coefficient": 1 if leaf["metric"] == "operating_cash_flow" else -1,
            }
            for leaf in leaves
        ],
        "source_components": [
            {
                "metric": leaf["metric"],
                "value_decimal": leaf["value_decimal"],
                "unit": "USD",
                "currency": "USD",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
            }
            for leaf in leaves
        ],
    }
    data = {
        "rights": "PUBLIC_SEC_ONLY",
        "cik": "0000000001",
        "member_sha256": "a" * 64,
        "observations": leaves,
        "policy": {},
    }
    report = certify_history(data, {"observations": [row]}, as_of=CUTOFF)
    assert report["observations"][0]["status"] == "UNAVAILABLE"
