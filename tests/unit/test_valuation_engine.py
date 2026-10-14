import sys
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from valuation_engine import calculate


def obs(value, **kwargs):
    return dict(
        value_decimal=str(value),
        unit="USD",
        currency="USD",
        period_start="2024-01-01",
        period_end="2024-12-31",
        frequency="annual",
        available_at="2025-02-01T00:00:00Z",
        source_ids=["source-" + str(value)],
        quality="INDEPENDENTLY_RECONCILED",
        **kwargs,
    )


def test_three_real_financial_formulas_remain_exact_and_identified():
    inputs = {
        "revenue": obs("100000000000000000003"),
        "operating_income": obs("20000000000000000001"),
        "net_income": obs("-3000000000000000001"),
    }
    for metric, numerator in [
        ("operating_margin", 20000000000000000001),
        ("net_margin", -3000000000000000001),
    ]:
        result = calculate(metric, inputs, as_of="2025-03-01T00:00:00Z")
        assert result["quality"] == "DERIVATION_VERIFIED"
        assert Fraction(
            int(result["value_numerator"]), int(result["value_denominator"])
        ) == Fraction(numerator, 100000000000000000003)
        assert result["formula_version"] and result["source_ids"]
    prior = obs("80000000000000000000")
    prior.update(period_start="2023-01-01", period_end="2023-12-31")
    growth = calculate(
        "revenue_growth", dict(inputs, prior_revenue=prior), as_of="2025-03-01T00:00:00Z"
    )
    assert Fraction(int(growth["value_numerator"]), int(growth["value_denominator"])) == Fraction(
        20000000000000000003, 80000000000000000000
    )


def test_missing_zero_denominator_currency_period_future_and_null_fail_closed():
    r = obs(100)
    n = obs(20)
    for bad, reason in [
        (dict(r, value_decimal="0"), "NONPOSITIVE_DENOMINATOR"),
        (dict(r, currency="EUR"), "CURRENCY_MISMATCH"),
        (dict(r, period_end="2023-12-31"), "PERIOD_MISMATCH"),
        (dict(r, available_at="2027-01-01T00:00:00Z"), "INPUT_NOT_AVAILABLE"),
        (dict(r, value_decimal=None), "NULL_INPUT"),
    ]:
        result = calculate(
            "operating_margin",
            {"revenue": bad, "operating_income": n},
            as_of="2025-03-01T00:00:00Z",
        )
        assert result["quality"] == "UNAVAILABLE" and reason in result["reasons"]
        assert result["value_decimal"] is None


def test_market_cap_never_substitutes_weighted_shares_or_unknown_identity():
    price = dict(
        obs(10),
        unit="USD/share",
        security_id="asset1",
        price_basis="RAW",
        adjustment_method="NONE",
        session_date="2025-02-01",
        valuation_at="2025-03-01T00:00:00Z",
    )
    shares = dict(
        obs(100),
        unit="shares",
        currency=None,
        security_id="asset1",
        definition="WEIGHTED_AVERAGE_DILUTED_SHARES",
    )
    result = calculate(
        "market_cap", {"price": price, "shares_outstanding": shares}, as_of="2025-03-01T00:00:00Z"
    )
    assert result["quality"] == "UNAVAILABLE"
    shares["definition"] = "COMMON_SHARES_OUTSTANDING"
    result = calculate(
        "market_cap", {"price": price, "shares_outstanding": shares}, as_of="2025-03-01T00:00:00Z"
    )
    assert "SECURITY_SCOPE_UNVERIFIED" in result["reasons"]


def test_ev_requires_every_reported_component_and_excludes_financials():
    inputs = {
        k: obs(v)
        for k, v in {
            "market_cap": 1000,
            "total_debt": 100,
            "cash": 50,
            "preferred_equity": 0,
            "noncontrolling_interest": 0,
        }.items()
    }
    inputs["total_debt"]["definition"] = "NONOVERLAPPING_INTEREST_BEARING_DEBT"
    inputs["market_cap"]["valuation_at"] = "2025-03-01T00:00:00Z"
    assert (
        calculate(
            "enterprise_value",
            inputs,
            as_of="2025-03-01T00:00:00Z",
            industry="nonfinancial_verified",
        )["value_decimal"]
        == "1050"
    )
    assert (
        calculate(
            "enterprise_value",
            {k: v for k, v in inputs.items() if k != "preferred_equity"},
            as_of="2025-03-01T00:00:00Z",
        )["quality"]
        == "UNAVAILABLE"
    )
    assert (
        calculate(
            "enterprise_value",
            inputs,
            as_of="2025-03-01T00:00:00Z",
            industry="financial_statement_evidence",
        )["quality"]
        == "EXCLUDED"
    )


def test_capex_cashflow_definition_and_income_attribution_are_required():
    result = calculate(
        "free_cash_flow",
        {"operating_cash_flow": obs(100), "capital_expenditures": obs(20)},
        as_of="2025-03-01T00:00:00Z",
    )
    assert result["quality"] == "UNAVAILABLE"
    capex = dict(obs(20), definition="CASH_PPE_PURCHASES_POSITIVE_OUTFLOW")
    assert (
        calculate(
            "free_cash_flow",
            {"operating_cash_flow": obs(100), "capital_expenditures": capex},
            as_of="2025-03-01T00:00:00Z",
        )["value_decimal"]
        == "80"
    )
    assert (
        calculate(
            "price_to_earnings",
            {"market_cap": obs(1000), "net_income": obs(100)},
            as_of="2025-03-01T00:00:00Z",
        )["quality"]
        == "UNAVAILABLE"
    )


def test_unknown_formula_rejected():
    with pytest.raises(ValueError):
        calculate("invented_ebitda", {}, as_of="2025-03-01T00:00:00Z")


def test_historical_multiples_cannot_use_future_financials_or_quarterly_flows():
    cap = dict(obs(1000), valuation_at="2025-01-01T00:00:00Z")
    revenue = obs(100)
    result = calculate(
        "price_to_sales", {"market_cap": cap, "revenue": revenue}, as_of="2025-03-01T00:00:00Z"
    )
    assert result["quality"] == "UNAVAILABLE" and "FINANCIAL_INPUT_AFTER_PRICE" in result["reasons"]
    cap["valuation_at"] = "2025-03-01T00:00:00Z"
    revenue["frequency"] = "quarterly"
    result = calculate(
        "price_to_sales", {"market_cap": cap, "revenue": revenue}, as_of="2025-03-01T00:00:00Z"
    )
    assert "ANNUAL_OR_TTM_REQUIRED" in result["reasons"]


def test_dimensionless_formula_rejects_per_share_revenue_and_exponent_bombs():
    r = dict(obs(100), unit="USD/share")
    result = calculate(
        "price_to_sales",
        {"market_cap": dict(obs(1000), valuation_at="2025-03-01T00:00:00Z"), "revenue": r},
        as_of="2025-03-01T00:00:00Z",
    )
    assert result["quality"] == "UNAVAILABLE" and "UNIT_MISMATCH" in result["reasons"]
    result = calculate(
        "net_margin", {"net_income": obs("1e999"), "revenue": obs(10)}, as_of="2025-03-01T00:00:00Z"
    )
    assert "INVALID_NUMERIC_ENCODING" in result["reasons"]


def test_adjusted_price_requires_separately_verified_share_adjustment_basis():
    price = dict(
        obs(10),
        unit="USD/share",
        security_id="asset1",
        session_date="2024-12-31",
        valuation_at="2025-03-01T00:00:00Z",
        price_basis="ADJUSTED",
        adjustment_method="SPLIT",
    )
    shares = dict(
        obs(100),
        unit="shares",
        currency=None,
        security_id="asset1",
        definition="COMMON_SHARES_OUTSTANDING",
    )
    result = calculate(
        "market_cap",
        {"price": price, "shares_outstanding": shares},
        as_of="2025-03-01T00:00:00Z",
        issuer_security_scope="SINGLE_COMMON_CLASS_VERIFIED",
    )
    assert (
        result["quality"] == "UNAVAILABLE"
        and "PRICE_SHARE_ADJUSTMENT_BASIS_UNVERIFIED" in result["reasons"]
    )
