"""Exact, versioned research formulas. Missing economic definitions never become zero."""

import re
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction

from temporal_core import normalize_source_instant

VERSION = "financial-calculations-v1"
FORMULAS = {
    "revenue_growth": ("revenue", "prior_revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "net_margin": ("net_income", "revenue"),
    "free_cash_flow": ("operating_cash_flow", "capital_expenditures"),
    "free_cash_flow_margin": ("free_cash_flow", "revenue"),
    "market_cap": ("price", "shares_outstanding"),
    "price_to_earnings": ("market_cap", "net_income"),
    "price_to_sales": ("market_cap", "revenue"),
    "enterprise_value": (
        "market_cap",
        "total_debt",
        "cash",
        "preferred_equity",
        "noncontrolling_interest",
    ),
    "ev_ebitda": ("enterprise_value", "ebitda"),
    "ev_revenue": ("enterprise_value", "revenue"),
    "free_cash_flow_yield": ("free_cash_flow", "market_cap"),
    "leverage": ("total_debt", "ebitda"),
}
FORMULA_EXPRESSIONS = {
    "revenue_growth": "revenue / prior_revenue - 1",
    "operating_margin": "operating_income / revenue",
    "net_margin": "net_income / revenue",
    "free_cash_flow": "operating_cash_flow - cash_PPE_purchase_outflow",
    "free_cash_flow_margin": "verified_free_cash_flow / revenue",
    "market_cap": "raw_price * same_date_same_class_outstanding_shares",
    "price_to_earnings": "market_cap / annual_or_TTM_income_attributable_to_common",
    "price_to_sales": "market_cap / annual_or_TTM_revenue",
    "enterprise_value": (
        "market_cap + nonoverlapping_debt + preferred_equity + noncontrolling_interest - cash"
    ),
    "ev_ebitda": "enterprise_value / source_defined_annual_or_TTM_EBITDA",
    "ev_revenue": "enterprise_value / annual_or_TTM_revenue",
    "free_cash_flow_yield": "verified_annual_or_TTM_free_cash_flow / market_cap",
    "leverage": "nonoverlapping_debt / source_defined_annual_or_TTM_EBITDA",
}
EV_METRICS = {"enterprise_value", "ev_ebitda", "ev_revenue", "leverage"}


def value(row):
    token = row.get("value_decimal")
    if not isinstance(token, str) or not re.fullmatch(r"-?\d{1,80}(?:\.\d{1,40})?", token):
        raise ValueError("NULL_INPUT" if token is None else "INVALID_NUMERIC_ENCODING")
    try:
        number = Decimal(token)
    except InvalidOperation as exc:
        raise ValueError("INVALID_NUMERIC_ENCODING") from exc
    if not number.is_finite():
        raise ValueError("NONFINITE_INPUT")
    return Fraction(number)


def decimal_if_terminating(number):
    rest = number.denominator
    while rest % 2 == 0:
        rest //= 2
    while rest % 5 == 0:
        rest //= 5
    if rest != 1:
        return None
    with localcontext() as context:
        context.prec = 256
        return format(Decimal(number.numerator) / Decimal(number.denominator), "f")


def calculate(metric, inputs, *, as_of, industry=None, issuer_security_scope=None):
    if metric not in FORMULAS:
        raise ValueError("UNSUPPORTED_FORMULA")
    roles = FORMULAS[metric]
    result = dict(
        metric=metric,
        formula_id=metric,
        formula_version=VERSION,
        formula_expression=FORMULA_EXPRESSIONS[metric],
        value_decimal=None,
        value_numerator=None,
        value_denominator=None,
        unit="USD" if metric in {"market_cap", "enterprise_value", "free_cash_flow"} else "ratio",
        currency=None,
        inputs={k: inputs.get(k) for k in roles},
        source_ids=[],
        quality="UNAVAILABLE",
        reasons=[],
        as_of=as_of,
        certification="CALCULATION_CHECKS_ONLY;NOT_ACCOUNTING_CERTIFICATION",
    )
    if metric in EV_METRICS and industry in {"financial_statement_evidence", "bank", "insurance"}:
        return dict(
            result, quality="EXCLUDED", reasons=["FINANCIAL_INDUSTRY_EV_DEFINITION_EXCLUDED"]
        )
    if metric in EV_METRICS and industry != "nonfinancial_verified":
        return dict(result, reasons=["NONFINANCIAL_INDUSTRY_UNVERIFIED"])
    try:
        cutoff = normalize_source_instant(as_of).epoch_ns
        numbers = {}
        for role in roles:
            row = inputs.get(role)
            if not isinstance(row, dict):
                raise ValueError("MISSING_COMPONENT:" + role)
            if row.get("quality") in {"UNAVAILABLE", "AMBIGUOUS", "EXCLUDED", "FAIL"}:
                raise ValueError("INPUT_QUALITY_NOT_PERMITTED:" + role)
            ids = row.get("source_ids")
            if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids):
                raise ValueError("INPUT_PROVENANCE_MISSING:" + role)
            if not row.get("available_at"):
                raise ValueError("INPUT_AVAILABILITY_UNESTABLISHED:" + role)
            if normalize_source_instant(row["available_at"]).epoch_ns > cutoff:
                raise ValueError("INPUT_NOT_AVAILABLE")
            numbers[role] = value(row)
            result["source_ids"].extend(ids)
        result["source_ids"] = sorted(set(result["source_ids"]))
        currencies = {inputs[k].get("currency") for k in roles if k != "shares_outstanding"}
        if None in currencies or "UNKNOWN" in currencies:
            raise ValueError("CURRENCY_UNESTABLISHED")
        if len(currencies) != 1:
            raise ValueError("CURRENCY_MISMATCH")
        result["currency"] = next(iter(currencies))
        for role in roles:
            expected_unit = (
                "shares"
                if role == "shares_outstanding"
                else result["currency"] + "/share"
                if role == "price"
                else result["currency"]
            )
            if inputs[role].get("unit") != expected_unit:
                raise ValueError("UNIT_MISMATCH")
        if (
            "free_cash_flow" in roles
            and inputs["free_cash_flow"].get("definition")
            != "OPERATING_CASH_FLOW_MINUS_CASH_PPE_PURCHASES_VERIFIED"
        ):
            raise ValueError("FCF_DEFINITION_UNVERIFIED")
        if "market_cap" in roles and numbers["market_cap"] <= 0:
            raise ValueError("NONPOSITIVE_EQUITY_INPUT")
        if metric in {
            "price_to_earnings",
            "price_to_sales",
            "ev_ebitda",
            "ev_revenue",
            "free_cash_flow_yield",
            "leverage",
        }:
            from datetime import date

            for role in roles:
                if role in {"net_income", "revenue", "free_cash_flow", "ebitda"}:
                    row = inputs[role]
                    if row.get("frequency") not in {"annual", "ttm"}:
                        raise ValueError("ANNUAL_OR_TTM_REQUIRED")
                    duration = (
                        date.fromisoformat(row["period_end"])
                        - date.fromisoformat(row["period_start"])
                    ).days + 1
                    if not 350 <= duration <= 378:
                        raise ValueError("ANNUAL_OR_TTM_DURATION_UNSUPPORTED")
        if metric == "leverage" and inputs["total_debt"].get("period_end") != inputs["ebitda"].get(
            "period_end"
        ):
            raise ValueError("BALANCE_FLOW_PERIOD_MISMATCH")
        market_role = (
            "price"
            if metric == "market_cap"
            else "market_cap"
            if "market_cap" in roles
            else "enterprise_value"
            if "enterprise_value" in roles
            else None
        )
        if market_role:
            valuation_at = inputs[market_role].get("valuation_at")
            if not valuation_at:
                raise ValueError("PRICE_VALUATION_TIME_UNESTABLISHED")
            price_time = normalize_source_instant(valuation_at).epoch_ns
            if price_time > cutoff:
                raise ValueError("PRICE_AFTER_CUTOFF")
            if any(
                normalize_source_instant(inputs[k]["available_at"]).epoch_ns > price_time
                for k in roles
                if k != market_role
            ):
                raise ValueError("FINANCIAL_INPUT_AFTER_PRICE")
            result["valuation_at"] = valuation_at
            for role in roles:
                if role in {"net_income", "revenue", "free_cash_flow", "ebitda"} and inputs[
                    role
                ].get("frequency") not in {"annual", "ttm"}:
                    raise ValueError("ANNUAL_OR_TTM_REQUIRED")
        if metric in {"operating_margin", "net_margin", "free_cash_flow", "free_cash_flow_margin"}:
            periods = {
                (
                    inputs[k].get("period_start"),
                    inputs[k].get("period_end"),
                    inputs[k].get("frequency"),
                )
                for k in roles
            }
            if len(periods) != 1 or any(x is None for x in next(iter(periods))):
                raise ValueError("PERIOD_MISMATCH")
            if any(inputs[k].get("unit") != result["currency"] for k in roles):
                raise ValueError("UNIT_MISMATCH")
        if metric == "revenue_growth":
            a, b = inputs["revenue"], inputs["prior_revenue"]
            from datetime import date

            if (
                a.get("unit") != b.get("unit")
                or a.get("frequency") != b.get("frequency")
                or a.get("period_start") is None
                or b.get("period_start") is None
            ):
                raise ValueError("PERIOD_MISMATCH")
            ends = (date.fromisoformat(a["period_end"]) - date.fromisoformat(b["period_end"])).days
            lengths = [
                (date.fromisoformat(r["period_end"]) - date.fromisoformat(r["period_start"])).days
                for r in (a, b)
            ]
            if not 350 <= ends <= 378 or abs(lengths[0] - lengths[1]) > 8:
                raise ValueError("NONCOMPARABLE_PRIOR_PERIOD")
            if numbers["prior_revenue"] <= 0:
                raise ValueError("NONPOSITIVE_DENOMINATOR")
            number = numbers["revenue"] / numbers["prior_revenue"] - 1
        elif metric == "free_cash_flow":
            if (
                inputs["capital_expenditures"].get("definition")
                != "CASH_PPE_PURCHASES_POSITIVE_OUTFLOW"
                or numbers["capital_expenditures"] < 0
            ):
                raise ValueError("CAPEX_DEFINITION_UNVERIFIED")
            number = numbers["operating_cash_flow"] - numbers["capital_expenditures"]
        elif metric == "market_cap":
            if (
                inputs["price"].get("price_basis") != "RAW"
                or inputs["price"].get("adjustment_method") != "NONE"
            ):
                raise ValueError("PRICE_SHARE_ADJUSTMENT_BASIS_UNVERIFIED")
            if inputs["shares_outstanding"].get("definition") != "COMMON_SHARES_OUTSTANDING":
                raise ValueError("OUTSTANDING_SHARE_DEFINITION_REQUIRED")
            if issuer_security_scope != "SINGLE_COMMON_CLASS_VERIFIED":
                raise ValueError("SECURITY_SCOPE_UNVERIFIED")
            if inputs["price"].get("security_id") != inputs["shares_outstanding"].get(
                "security_id"
            ) or not inputs["price"].get("security_id"):
                raise ValueError("SECURITY_SCOPE_MISMATCH")
            if (
                inputs["price"].get("unit") != result["currency"] + "/share"
                or inputs["shares_outstanding"].get("unit") != "shares"
            ):
                raise ValueError("UNIT_MISMATCH")
            if numbers["price"] <= 0 or numbers["shares_outstanding"] <= 0:
                raise ValueError("NONPOSITIVE_EQUITY_INPUT")
            if inputs["shares_outstanding"].get("period_end") != inputs["price"].get(
                "session_date"
            ):
                raise ValueError("SAME_DATE_SHARE_COUNT_REQUIRED")
            number = numbers["price"] * numbers["shares_outstanding"]
        elif metric == "enterprise_value":
            if inputs["total_debt"].get("definition") != "NONOVERLAPPING_INTEREST_BEARING_DEBT":
                raise ValueError("DEBT_COMPONENT_OVERLAP_UNRESOLVED")
            balance_dates = {
                inputs[k].get("period_end")
                for k in ("total_debt", "cash", "preferred_equity", "noncontrolling_interest")
            }
            if len(balance_dates) != 1 or None in balance_dates:
                raise ValueError("BALANCE_PERIOD_MISMATCH")
            number = (
                sum(
                    numbers[k]
                    for k in (
                        "market_cap",
                        "total_debt",
                        "preferred_equity",
                        "noncontrolling_interest",
                    )
                )
                - numbers["cash"]
            )
        else:
            first, second = roles
            if (
                metric == "price_to_earnings"
                and inputs["net_income"].get("definition") != "NET_INCOME_ATTRIBUTABLE_TO_COMMON"
            ):
                raise ValueError("COMMON_INCOME_SCOPE_UNVERIFIED")
            if second == "ebitda" and inputs["ebitda"].get("definition") not in {
                "REPORTED_RECONCILED_EBITDA",
                "OPERATING_INCOME_PLUS_DA_RECONCILED",
            }:
                raise ValueError("EBITDA_DEFINITION_UNVERIFIED")
            if (
                metric == "leverage"
                and inputs["total_debt"].get("definition") != "NONOVERLAPPING_INTEREST_BEARING_DEBT"
            ):
                raise ValueError("DEBT_COMPONENT_OVERLAP_UNRESOLVED")
            if numbers[second] <= 0:
                raise ValueError("NONPOSITIVE_DENOMINATOR")
            number = numbers[first] / numbers[second]
        result.update(
            value_decimal=decimal_if_terminating(number),
            value_numerator=str(number.numerator),
            value_denominator=str(number.denominator),
            quality="DERIVATION_VERIFIED",
            available_at=max(
                (inputs[k]["available_at"] for k in roles),
                key=lambda x: normalize_source_instant(x).epoch_ns,
            ),
            reasons=[],
        )
        for field in ("period_start", "period_end", "frequency"):
            result[field] = inputs[roles[-1]].get(field)
        return result
    except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
        return dict(result, reasons=[str(exc) or "INVALID_INPUT_CONTEXT"])
