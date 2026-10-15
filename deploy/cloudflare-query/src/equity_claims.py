"""Versioned filing-backed equity claims from immutable SEC company facts.

Native Zion capability for the enterprise-to-equity bridge. Every claim carries an
explicit evidence state; a concept that is absent is reported as REVIEWED_ABSENT
only after the whole candidate concept list for that claim has been evaluated over
the eligible point-in-time facts, and never as an assumed zero. Concepts that the
public Company Facts API does not expose (per-class share counts, conversion rates
carried in company-extension tags) stay UNAVAILABLE; they are never inferred.

Selection is point-in-time and fails closed: only facts filed on or before the
valuation date are eligible, the latest period end wins, ties resolve to the latest
filing, and facts older than MAX_AGE_DAYS are not current. Duration facts and
dimensional facts are excluded. Every selected fact keeps its concept, unit, exact
reported token, period end, form, accession, filing date and an evidence digest.

Policy, stated once so the consumer never has to guess:
- debt is interest-bearing borrowings only, excluding leases. A filer-stated
  current-debt total (DebtCurrent) is used as the current claim when reported, and
  any component that could already be inside it is reported as a potential
  duplicate instead of being added again.
- finance leases are a separate claim; operating leases are classified separately
  and are excluded from the enterprise bridge because EBIT is already after
  operating lease cost.
- cash is cash and cash equivalents only. Nonoperating assets add short-term
  investments or current marketable securities (whichever the filer reports, never
  both) plus noncurrent marketable securities. Long-term investments are excluded.
- preferred equity keeps carrying value, liquidation preference and conversion
  evidence as distinct measures. Caller-supplied offering terms are marked ASSUMED,
  are cross-checked against reported facts, and a material disagreement yields
  CONFLICTING with permitted_for_valuation false.
- noncontrolling interests are reported from whichever measure the filer files,
  including the VIE disclosure measure, with the treatment recorded. A redeemable
  subset that Company Facts does not expose is UNAVAILABLE, never added to the
  total, and never subtracted twice.
"""

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

VERSION = "zion-equity-claims-v1"
EVIDENCE_STATES = (
    "REPORTED",
    "DERIVED_VERIFIED",
    "REVIEWED_ABSENT",
    "ASSUMED",
    "CONFLICTING",
    "UNAVAILABLE",
)
CERTIFIED_STATES = ("REPORTED", "DERIVED_VERIFIED", "REVIEWED_ABSENT")
MAX_AGE_DAYS = 400
PREFERRED_SHARE_ROUNDING_TOLERANCE = 500000
DEDUCT_FROM_ENTERPRISE_VALUE = ("debt", "finance_leases", "preferred_equity", "noncontrolling_interests")
ADD_TO_ENTERPRISE_VALUE = ("cash", "nonoperating_assets")

DEBT_CURRENT_TOTAL = ("DebtCurrent",)
DEBT_CURRENT_PARTS = ("LongTermDebtCurrent",)
CURRENT_BORROWINGS = ("CommercialPaper", "ShortTermBorrowings", "OtherShortTermBorrowings")
DEBT_NONCURRENT = ("LongTermDebtNoncurrent",)
DEBT_COMBINED_TOTAL = ("LongTermDebt",)
FINANCE_LEASE_TOTAL = ("FinanceLeaseLiability", "CapitalLeaseObligations")
FINANCE_LEASE_PARTS = (
    "FinanceLeaseLiabilityCurrent",
    "FinanceLeaseLiabilityNoncurrent",
    "CapitalLeaseObligationsCurrent",
    "CapitalLeaseObligationsNoncurrent",
)
OPERATING_LEASE_TOTAL = ("OperatingLeaseLiability",)
OPERATING_LEASE_PARTS = ("OperatingLeaseLiabilityCurrent", "OperatingLeaseLiabilityNoncurrent")
CASH = ("CashAndCashEquivalentsAtCarryingValue",)
SHORT_INVESTMENTS = ("ShortTermInvestments", "MarketableSecuritiesCurrent")
NONCURRENT_SECURITIES = ("MarketableSecuritiesNoncurrent",)
EXCLUDED_INVESTMENTS = ("OtherLongTermInvestments", "EquitySecuritiesFvNi", "LongTermInvestments")
PREFERRED_CARRYING = (
    "ConvertiblePreferredStockNonredeemableOrRedeemableIssuerOptionValue",
    "PreferredStockValue",
    "PreferredStockValueOutstanding",
)
PREFERRED_SHARES = ("PreferredStockSharesOutstanding", "PreferredStockSharesIssued")
PREFERRED_LIQUIDATION_PER_SHARE = ("PreferredStockLiquidationPreference",)
PREFERRED_LIQUIDATION_TOTAL = ("PreferredStockLiquidationPreferenceValue",)
PREFERRED_DIVIDEND_RATE = ("PreferredStockDividendRatePercentage", "PreferredStockDividendRate")
PREFERRED_DIVIDEND_CASH = ("DividendsPreferredStockCash", "PreferredStockDividendsIncomeStatementImpact")
PREFERRED_INCREMENTAL_SHARES = ("IncrementalCommonSharesAttributableToConversionOfPreferredStock",)
NONCONTROLLING_TOTAL = ("MinorityInterest",)
NONCONTROLLING_VIE = ("NoncontrollingInterestInVariableInterestEntity",)
NONCONTROLLING_REDEEMABLE = (
    "RedeemableNoncontrollingInterestEquityCarryingAmount",
    "RedeemableNoncontrollingInterestEquityFairValue",
    "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
)
COMMON_SHARES_BALANCE_SHEET = ("CommonStockSharesOutstanding",)
REPORTED_EQUITY_TOTALS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)

UNITS = {
    "debt": "USD",
    "finance_leases": "USD",
    "operating_leases": "USD",
    "cash": "USD",
    "nonoperating_assets": "USD",
    "preferred_equity": "USD",
    "noncontrolling_interests": "USD",
    "common_shares": "shares",
}


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def decimal_token(value):
    """Exact canonical decimal string for a reported Company Facts token."""
    if isinstance(value, bool) or value is None:
        raise ValueError("INVALID_REPORTED_TOKEN")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("INVALID_REPORTED_TOKEN") from exc
    if not number.is_finite():
        raise ValueError("NONFINITE_REPORTED_TOKEN")
    return format(number.normalize(), "f")


def eligible(facts, concept, valuation_date, unit="USD", taxonomy="us-gaap", max_age_days=MAX_AGE_DAYS):
    """Eligible point-in-time instants: no duration, no dimension, on or before the date."""
    body = (facts or {}).get(taxonomy, {}).get(concept)
    if not isinstance(body, dict):
        return []
    rows = body.get("units", {}).get(unit)
    if not isinstance(rows, list):
        return []
    floor = valuation_date - timedelta(days=max_age_days)
    chosen = []
    for row in rows:
        if not isinstance(row, dict) or "val" not in row or "start" in row:
            continue
        if row.get("segment") is not None:
            continue
        end, filed = row.get("end"), row.get("filed")
        if not isinstance(end, str) or not isinstance(filed, str):
            continue
        try:
            end_date, filed_date = date.fromisoformat(end), date.fromisoformat(filed)
        except ValueError:
            continue
        if filed_date > valuation_date or not floor <= end_date <= valuation_date:
            continue
        chosen.append(row)
    return chosen


def source(concept, taxonomy, unit, row):
    """One selected fact with its filing identity and evidence digest."""
    body = {
        "concept": taxonomy + ":" + concept,
        "unit": unit,
        "reported_token": str(row["val"]),
        "period_end": row["end"],
        "form": row.get("form"),
        "accession": row.get("accn"),
        "filed": row.get("filed"),
    }
    return dict(
        body,
        value_decimal=decimal_token(row["val"]),
        available_at=row.get("filed"),
        availability_granularity="FILING_DATE_ONLY",
        evidence_digest=digest(dict(body, taxonomy=taxonomy, concept_short=concept)),
    )


def selected(facts, concept, valuation_date, unit="USD", taxonomy="us-gaap", max_age_days=MAX_AGE_DAYS):
    """Latest period end wins; ties resolve to the latest filing, then the highest accession."""
    rows = eligible(facts, concept, valuation_date, unit, taxonomy, max_age_days)
    if not rows:
        return None
    end = max(row["end"] for row in rows)
    row = max(
        (row for row in rows if row["end"] == end),
        key=lambda row: (row["filed"], str(row.get("accn", ""))),
    )
    return source(concept, taxonomy, unit, row)


def group(facts, concepts, valuation_date, unit="USD", taxonomy="us-gaap"):
    found = {}
    for concept in concepts:
        value = selected(facts, concept, valuation_date, unit, taxonomy)
        if value is not None:
            found[concept] = value
    return found


def period_eligible(facts, concept, valuation_date, unit="USD", taxonomy="us-gaap", max_age_days=MAX_AGE_DAYS):
    """Eligible period facts for measures that have no instant context, such as a dividend rate."""
    body = (facts or {}).get(taxonomy, {}).get(concept)
    if not isinstance(body, dict):
        return []
    rows = body.get("units", {}).get(unit)
    if not isinstance(rows, list):
        return []
    floor = valuation_date - timedelta(days=max_age_days)
    chosen = []
    for row in rows:
        if not isinstance(row, dict) or "val" not in row or row.get("segment") is not None:
            continue
        end, filed = row.get("end"), row.get("filed")
        if not isinstance(end, str) or not isinstance(filed, str):
            continue
        try:
            end_date, filed_date = date.fromisoformat(end), date.fromisoformat(filed)
        except ValueError:
            continue
        if filed_date > valuation_date or not floor <= end_date <= valuation_date:
            continue
        chosen.append(row)
    return chosen


def period_selected(facts, concepts, valuation_date, unit="USD", taxonomy="us-gaap"):
    """Latest eligible period measure, labelled as a period measure rather than an instant."""
    found = {}
    for concept in concepts:
        rows = period_eligible(facts, concept, valuation_date, unit, taxonomy)
        if not rows:
            continue
        end = max(row["end"] for row in rows)
        row = max(
            (row for row in rows if row["end"] == end),
            key=lambda row: (row["filed"], str(row.get("accn", ""))),
        )
        found[concept] = dict(
            source(concept, taxonomy, unit, row),
            measure_kind="PERIOD_MEASURE",
            period_start=row.get("start"),
        )
    return found


def observations(facts, concept, valuation_date, unit="shares", taxonomy="us-gaap", limit=12):
    """Dated point-in-time observations, newest first, so no caller has to guess the vintage."""
    rows = eligible(facts, concept, valuation_date, unit, taxonomy)
    ordered = sorted(rows, key=lambda row: (row["end"], row["filed"], str(row.get("accn", ""))), reverse=True)
    seen = []
    for row in ordered:
        if len(seen) >= limit:
            break
        seen.append(source(concept, taxonomy, unit, row))
    return seen


def claim(value_decimal, *, unit, measure, state, sources, selection_rule, notes=(), extra=None):
    body = {
        "value_decimal": value_decimal,
        "unit": unit,
        "evidence_state": state,
        "measure": measure,
        "selection_rule": selection_rule,
        "period_end": max((s["period_end"] for s in sources), default=None),
        "available_at": max((s["available_at"] for s in sources), default=None) if sources else None,
        "availability_granularity": "FILING_DATE_ONLY",
        "sources": sorted(sources, key=lambda s: s["concept"]),
        "notes": list(notes),
    }
    if extra:
        body.update(extra)
    body["evidence_digest"] = digest(body)
    return body


def absent(measure, selection_rule, concepts, note):
    return claim(
        None,
        unit="USD",
        measure=measure,
        state="REVIEWED_ABSENT",
        sources=[],
        selection_rule=selection_rule,
        notes=[
            note + " A missing concept is not evidence of a zero claim.",
            "Candidate concepts reviewed: " + ", ".join(concepts) + ".",
        ],
        extra={"permitted_for_valuation": True, "verified_absent": True},
    )


def sum_tokens(tokens):
    total = Decimal(0)
    for token in tokens:
        total += Decimal(token)
    return format(total.normalize(), "f")


def _total_or_parts(facts, valuation_date, total_concepts, part_concepts, unit="USD"):
    """Total when reported, otherwise the nonoverlapping parts, with mismatch notes."""
    total = group(facts, total_concepts, valuation_date, unit)
    parts = group(facts, part_concepts, valuation_date, unit)
    notes = []
    if total:
        concept, fact = sorted(total.items())[0]
        if len(total) > 1:
            notes.append(
                "Multiple totals reported ("
                + ", ".join(sorted(total))
                + "); used "
                + concept
                + "."
            )
        if parts:
            same = {k: v for k, v in parts.items() if v["period_end"] == fact["period_end"]}
            if same:
                gap = Decimal(fact["value_decimal"]) - sum(
                    Decimal(v["value_decimal"]) for v in same.values()
                )
                if gap:
                    notes.append(
                        "Reported total differs from its same-period components by "
                        + format(gap.normalize(), "f")
                        + " at "
                        + fact["period_end"]
                        + "; the total is used and the components are not added."
                    )
            other = {k: v for k, v in parts.items() if v["period_end"] != fact["period_end"]}
            if other:
                notes.append(
                    "Components at a different period ("
                    + ", ".join(sorted(other))
                    + ") are not combined with the total."
                )
        return Decimal(fact["value_decimal"]), [fact], notes
    if parts:
        ends = {v["period_end"] for v in parts.values()}
        if len(ends) > 1:
            notes.append(
                "Components span multiple period ends ("
                + ", ".join(sorted(ends))
                + "); components are summed as reported and flagged for review."
            )
        return (
            sum(Decimal(v["value_decimal"]) for v in parts.values()),
            list(parts.values()),
            notes,
        )
    return None, [], notes


def debt_claim(facts, valuation_date):
    """Interest-bearing borrowings only, excluding leases."""
    rule = (
        "Filer-stated current-debt total when reported, otherwise the current portion plus "
        "current borrowings, plus the noncurrent portion."
    )
    current_total = group(facts, DEBT_CURRENT_TOTAL, valuation_date)
    current_parts = group(facts, DEBT_CURRENT_PARTS, valuation_date)
    borrowings = group(facts, CURRENT_BORROWINGS, valuation_date)
    noncurrent = group(facts, DEBT_NONCURRENT, valuation_date)
    combined = group(facts, DEBT_COMBINED_TOTAL, valuation_date)
    notes = []
    conflicting = False
    candidates = []
    duplicates = []
    if current_total:
        concept, fact = sorted(current_total.items())[0]
        current = Decimal(fact["value_decimal"])
        sources = [fact]
        for name, value in sorted({**current_parts, **borrowings}.items()):
            if value["period_end"] != fact["period_end"]:
                continue
            # Reported at the same period end, so it may already be inside the filer's total.
            # It is recorded as a duplicate and never added, whether or not it equals the total.
            duplicates.append(value)
            if Decimal(value["value_decimal"]) == current:
                continue
            notes.append(
                "Potential duplicate: "
                + name
                + " is reported at the same period end ("
                + fact["period_end"]
                + ") and may already be inside "
                + concept
                + "; it is not added."
            )
            candidates.append(value)
            if Decimal(value["value_decimal"]) > current:
                conflicting = True
    else:
        current = Decimal(0)
        sources = []
        for name, value in sorted({**current_parts, **borrowings}.items()):
            current += Decimal(value["value_decimal"])
            sources.append(value)
        if not sources:
            notes.append("No current debt concept is reported.")
    if noncurrent:
        concept, fact = sorted(noncurrent.items())[0]
        value = Decimal(fact["value_decimal"])
        sources.append(fact)
    else:
        value = Decimal(0)
    if combined and not noncurrent:
        concept, fact = sorted(combined.items())[0]
        notes.append(
            concept
            + " is used because the noncurrent component is absent; a combined total can include "
            + "the current portion, so the current claim is taken as reported alongside it."
        )
        value = Decimal(fact["value_decimal"])
        sources.append(fact)
    elif combined and noncurrent:
        concept, fact = sorted(combined.items())[0]
        # A combined total is only comparable to the same-scope components (the long-term
        # current portion plus the noncurrent portion); current borrowings are a different scope.
        comparable = {**current_parts, **noncurrent}
        same = [f for f in comparable.values() if f["period_end"] == fact["period_end"]]
        if same and current_parts:
            gap = Decimal(fact["value_decimal"]) - sum(Decimal(f["value_decimal"]) for f in same)
            if gap:
                notes.append(
                    concept
                    + " differs from the reported components by "
                    + format(gap.normalize(), "f")
                    + " at "
                    + fact["period_end"]
                    + "; components are used."
                )
        elif same and "us-gaap:" + sorted(current_total)[0] in {
            "us-gaap:" + name for name in current_total
        }:
            notes.append(
                concept
                + " is reported alongside a filer-stated current-debt total, which is a different "
                + "scope; the total is not used as a component comparison."
            )
        else:
            notes.append(
                concept
                + " is from "
                + fact["period_end"]
                + ", a different period than the components; the total is ignored."
            )
    if not sources:
        return absent(
            "Interest-bearing borrowings, excluding leases.",
            rule,
            DEBT_CURRENT_TOTAL + DEBT_CURRENT_PARTS + CURRENT_BORROWINGS + DEBT_NONCURRENT + DEBT_COMBINED_TOTAL,
            "No borrowings concept is reported in the eligible window.",
        )
    split = {
        "current_decimal": format(current.normalize(), "f") if sources else None,
        "noncurrent_decimal": format(value.normalize(), "f") if sources else None,
        "potential_duplicate_concepts": sorted({c["concept"] for c in duplicates}),
        "conflicting_concepts": sorted({c["concept"] for c in candidates}),
    }
    if conflicting:
        return claim(
            None,
            unit="USD",
            measure="Interest-bearing borrowings, excluding leases.",
            state="CONFLICTING",
            sources=sources + candidates,
            selection_rule=rule,
            notes=notes + ["A reported component exceeds the filer-stated current-debt total."],
            extra=dict(split, permitted_for_valuation=False, candidates=candidates),
        )
    return claim(
        format((current + value).normalize(), "f"),
        unit="USD",
        measure="Interest-bearing borrowings, excluding leases.",
        state="REPORTED",
        sources=sources,
        selection_rule=rule,
        notes=notes,
        extra=split,
    )


def finance_lease_claim(facts, valuation_date):
    rule = "Reported finance/capital lease total, otherwise its nonoverlapping current and noncurrent parts."
    value, sources, notes = _total_or_parts(facts, valuation_date, FINANCE_LEASE_TOTAL, FINANCE_LEASE_PARTS)
    split_current = next(
        (s["value_decimal"] for s in sources if s["concept"].endswith("Current")), None
    )
    split_noncurrent = next(
        (s["value_decimal"] for s in sources if s["concept"].endswith("Noncurrent")), None
    )
    if value is not None and (split_current is None or split_noncurrent is None):
        # The split is evidence, not a second claim: the parts are reported for the schedule
        # and are never added to the total they belong to.
        parts = group(facts, FINANCE_LEASE_PARTS, valuation_date)
        same = {k: v for k, v in parts.items() if v["period_end"] == sorted(sources, key=lambda s: s["period_end"] or "")[0]["period_end"]}
        if same:
            gap = value - sum(Decimal(v["value_decimal"]) for v in same.values())
            if gap:
                notes.append(
                    "Reported components differ from the total by "
                    + format(gap.normalize(), "f")
                    + "; the total is the claim and the components are not added."
                )
            else:
                notes.append(
                    "The reported current and noncurrent components sum to the total and supply the "
                    "split without being added to it."
                )
            split_current = next(
                (v["value_decimal"] for k, v in sorted(same.items()) if k.endswith("Current")), None
            )
            split_noncurrent = next(
                (v["value_decimal"] for k, v in sorted(same.items()) if k.endswith("Noncurrent")), None
            )
    if value is None:
        return absent(
            "Finance lease liabilities.", rule, FINANCE_LEASE_TOTAL + FINANCE_LEASE_PARTS,
            "No finance lease liability concept is reported in the eligible window.",
        )
    return claim(
        format(value.normalize(), "f"),
        unit="USD",
        measure="Finance lease liabilities.",
        state="REPORTED",
        sources=sources,
        selection_rule=rule,
        notes=notes,
        extra={"current_decimal": split_current, "noncurrent_decimal": split_noncurrent},
    )


def operating_lease_claim(facts, valuation_date):
    rule = "Reported operating lease liability total, otherwise its current and noncurrent parts."
    value, sources, notes = _total_or_parts(facts, valuation_date, OPERATING_LEASE_TOTAL, OPERATING_LEASE_PARTS)
    if value is None:
        return absent(
            "Operating lease liabilities.", rule, OPERATING_LEASE_TOTAL + OPERATING_LEASE_PARTS,
            "No operating lease liability concept is reported in the eligible window.",
        )
    return claim(
        format(value.normalize(), "f"),
        unit="USD",
        measure="Operating lease liabilities, classified separately from debt.",
        state="REPORTED",
        sources=sources,
        selection_rule=rule,
        notes=notes
        + [
            "Excluded from the enterprise bridge: operating lease cost is already inside EBIT, so "
            "the liability is not subtracted again."
        ],
        extra={"included_in_enterprise_bridge": False},
    )


def cash_claim(facts, valuation_date):
    rule = "Cash and cash equivalents only; restricted cash and marketable securities are not included."
    found = group(facts, CASH, valuation_date)
    if not found:
        return absent(
            "Cash and cash equivalents.", rule, CASH,
            "No cash and cash equivalents concept is reported in the eligible window.",
        )
    concept, fact = sorted(found.items())[0]
    return claim(
        fact["value_decimal"],
        unit="USD",
        measure="Cash and cash equivalents.",
        state="REPORTED",
        sources=[fact],
        selection_rule=rule,
        notes=[] if len(found) == 1 else ["Additional cash concepts reported: " + ", ".join(sorted(set(found) - {concept})) + "."],
    )


def nonoperating_asset_claim(facts, valuation_date):
    rule = (
        "Short-term investments or current marketable securities (whichever is reported, never both) "
        "plus noncurrent marketable securities."
    )
    cash = group(facts, CASH, valuation_date)
    short = group(facts, SHORT_INVESTMENTS, valuation_date)
    noncurrent = group(facts, NONCURRENT_SECURITIES, valuation_date)
    excluded = group(facts, EXCLUDED_INVESTMENTS, valuation_date)
    sources = []
    notes = []
    total = Decimal(0)
    for name, value in sorted(cash.items()):
        total += Decimal(value["value_decimal"])
        sources.append(value)
    if short:
        used = sorted(short)[0]
        total += Decimal(short[used]["value_decimal"])
        sources.append(short[used])
        unused = sorted(set(short) - {used})
        if unused:
            notes.append(
                "Also reported: "
                + ", ".join(unused)
                + " at "
                + short[unused[0]]["period_end"]
                + "; not added, because it measures the same securities."
            )
    for name, value in sorted(noncurrent.items()):
        total += Decimal(value["value_decimal"])
        sources.append(value)
    if excluded:
        notes.append(
            "Excluded by policy: "
            + ", ".join(sorted(fact["concept"] for fact in excluded.values()))
            + " (long-term and equity investments are not treated as cash equivalents)."
        )
    if not sources:
        return absent(
            "Cash, cash equivalents and marketable securities.", rule,
            CASH + SHORT_INVESTMENTS + NONCURRENT_SECURITIES,
            "No nonoperating asset concept is reported in the eligible window.",
        )
    return claim(
        format(total.normalize(), "f"),
        unit="USD",
        measure="Cash, cash equivalents and marketable securities treated as nonoperating.",
        state="REPORTED",
        sources=sources,
        selection_rule=rule,
        notes=notes,
        extra={"excluded_concepts": sorted(fact["concept"] for fact in excluded.values())},
    )


def _dilution_terms(terms):
    """Offering terms are an explicit caller assumption, never a filing-derived value."""
    if not isinstance(terms, dict) or not isinstance(terms.get("series"), list) or not terms["series"]:
        raise ValueError("INVALID_PREFERRED_TERMS")
    preferred_shares = Decimal(0)
    liquidation = Decimal(0)
    minimum = Decimal(0)
    maximum = Decimal(0)
    for series in terms["series"]:
        per_depositary = Decimal(str(series["depositary_per_preferred"]))
        if per_depositary <= 0:
            raise ValueError("INVALID_PREFERRED_TERMS")
        count = Decimal(str(series["depositary_shares"])) / per_depositary
        preferred_shares += count
        liquidation += count * Decimal(str(series["liquidation_preference_per_share"]))
        minimum += count * Decimal(str(series["min_rate_per_preferred"]))
        maximum += count * Decimal(str(series["max_rate_per_preferred"]))
    return {
        "preferred_shares_decimal": format(preferred_shares.normalize(), "f"),
        "liquidation_preference_total_decimal": format(liquidation.normalize(), "f"),
        "min_converted_common_shares_decimal": format(minimum.normalize(), "f"),
        "max_converted_common_shares_decimal": format(maximum.normalize(), "f"),
        "terms": terms,
    }


def first_of(bucket, order):
    """First concept in the declared preference order, so selection never depends on sorting."""
    for concept in order:
        if concept in bucket:
            return concept, bucket[concept]
    if bucket:
        concept = sorted(bucket)[0]
        return concept, bucket[concept]
    return None, None


def preferred_claim(facts, valuation_date, terms=None):
    rule = (
        "Reported carrying value, share count, liquidation preference per share, dividend rate and "
        "conversion evidence are kept as distinct measures and are never substituted for one another."
    )
    carrying = group(facts, PREFERRED_CARRYING, valuation_date)
    shares = group(facts, PREFERRED_SHARES, valuation_date, unit="shares")
    per_share = group(facts, PREFERRED_LIQUIDATION_PER_SHARE, valuation_date, unit="USD/shares")
    liquidation_total = group(facts, PREFERRED_LIQUIDATION_TOTAL, valuation_date)
    # A dividend rate, a dividend amount and an incremental share count are declared for a
    # period, not at an instant; they keep their period context and are labelled as such.
    rate = period_selected(facts, PREFERRED_DIVIDEND_RATE, valuation_date, unit="pure")
    dividends = period_selected(facts, PREFERRED_DIVIDEND_CASH, valuation_date)
    incremental = period_selected(facts, PREFERRED_INCREMENTAL_SHARES, valuation_date, unit="shares")
    sources = [
        fact
        for bucket in (carrying, shares, per_share, liquidation_total, rate, dividends, incremental)
        for fact in bucket.values()
    ]
    notes = []
    conflicts = []
    reported_shares = None
    carrying_concept, carrying_fact = first_of(carrying, PREFERRED_CARRYING)
    shares_concept, shares_fact = first_of(shares, PREFERRED_SHARES)
    preference_concept, preference_fact = first_of(per_share, PREFERRED_LIQUIDATION_PER_SHARE)
    preference_total_fact = first_of(liquidation_total, PREFERRED_LIQUIDATION_TOTAL)[1]
    rate_fact = first_of(rate, PREFERRED_DIVIDEND_RATE)[1]
    dividend_fact = first_of(dividends, PREFERRED_DIVIDEND_CASH)[1]
    incremental_fact = first_of(incremental, PREFERRED_INCREMENTAL_SHARES)[1]
    reported_shares = Decimal(shares_fact["value_decimal"]) if shares_fact else None
    if shares_fact and len(shares) > 1:
        other = sorted(set(shares) - {shares_concept})
        if any(Decimal(shares[k]["value_decimal"]) != reported_shares for k in other):
            conflicts.append(
                "Reported preferred share counts disagree: " + ", ".join(sorted(shares)) + "."
            )
    if preference_fact:
        notes.append("Liquidation preference per share: " + preference_fact["value_decimal"] + " " + preference_fact["unit"] + ".")
    if preference_total_fact:
        notes.append("Reported liquidation preference total: " + preference_total_fact["value_decimal"] + ".")
    if incremental_fact:
        notes.append(
            "Incremental common shares attributable to preferred conversion are a period weighted "
            "measure, not the as-converted share count."
        )
    # The claim value is the reported carrying amount. Other preferred evidence does not create a
    # carrying claim by itself, and a filed zero carrying amount is a reported zero, not an absence.
    value = carrying_fact["value_decimal"] if carrying_fact else None
    state = "REPORTED" if carrying_fact else "REVIEWED_ABSENT"
    if carrying_fact and len(carrying) > 1:
        distinct = {v["value_decimal"] for v in carrying.values()}
        if len(distinct) > 1:
            conflicts.append("Reported preferred carrying values disagree: " + ", ".join(sorted(carrying)) + ".")
    body = dict(
        carrying_value_decimal=value,
        carrying_concept=carrying_fact["concept"] if carrying_fact else None,
        shares_outstanding_decimal=shares_fact["value_decimal"] if shares_fact else None,
        shares_concept=shares_fact["concept"] if shares_fact else None,
        liquidation_preference_per_share_decimal=preference_fact["value_decimal"] if preference_fact else None,
        liquidation_preference_per_share_unit=preference_fact["unit"] if preference_fact else None,
        liquidation_preference_total_decimal=preference_total_fact["value_decimal"] if preference_total_fact else None,
        dividend_rate_decimal=rate_fact["value_decimal"] if rate_fact else None,
        dividend_cash_decimal=dividend_fact["value_decimal"] if dividend_fact else None,
        incremental_converted_common_shares_decimal=incremental_fact["value_decimal"] if incremental_fact else None,
        dilution_terms=None,
        conflicts=conflicts,
        evidence_measures_reported=sorted({s["concept"] for s in sources}),
    )
    if terms is not None:
        try:
            dilution = _dilution_terms(terms)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError("INVALID_PREFERRED_TERMS") from exc
        implied = Decimal(dilution["preferred_shares_decimal"])
        term_state = "ASSUMED"
        if reported_shares is not None and abs(implied - reported_shares) > PREFERRED_SHARE_ROUNDING_TOLERANCE:
            term_state = "CONFLICTING"
            conflicts.append(
                "Caller-supplied terms imply "
                + format(implied.normalize(), "f")
                + " preferred shares but "
                + format(reported_shares.normalize(), "f")
                + " are reported."
            )
        if (
            body["liquidation_preference_per_share_decimal"]
            and Decimal(body["liquidation_preference_per_share_decimal"]) != Decimal(str(terms["series"][0]["liquidation_preference_per_share"]))
        ):
            conflicts.append("Caller-supplied liquidation preference per share differs from the reported value.")
        body["dilution_terms"] = dict(
            dilution,
            evidence_state=term_state,
            basis="EXPLICIT_SELECTION_FROM_OFFERING_DOCUMENT;NOT_XBRL_VERIFIED",
            permitted_for_valuation=term_state == "ASSUMED" or not conflicts,
        )
        if term_state == "CONFLICTING":
            state = "CONFLICTING"
        elif state == "REPORTED":
            state = "DERIVED_VERIFIED"
    if not carrying_fact:
        notes.append(
            (
                "No preferred carrying amount is reported in the eligible window; other preferred "
                "evidence is reported but is never converted into a carrying claim. A missing concept "
                "is not evidence of a zero claim."
            )
            if sources
            else "No preferred equity concept is reported in the eligible window. A missing concept "
            "is not evidence of a zero claim."
        )
        notes.append(
            "Candidate concepts reviewed: "
            + ", ".join(PREFERRED_CARRYING + PREFERRED_SHARES + PREFERRED_LIQUIDATION_PER_SHARE
                        + PREFERRED_LIQUIDATION_TOTAL + PREFERRED_DIVIDEND_RATE)
            + "."
        )
    return claim(
        value,
        unit="USD",
        measure="Preferred equity and convertible instruments.",
        state=state,
        sources=sources,
        selection_rule=rule,
        notes=notes,
        extra=dict(body, permitted_for_valuation=state != "CONFLICTING",
                   verified_absent=state == "REVIEWED_ABSENT"),
    )


def noncontrolling_claim(facts, valuation_date):
    """Reported from whichever measure the filer files, including the VIE disclosure measure."""
    rule = (
        "The reported noncontrolling interest total when present; otherwise the reported "
        "noncontrolling interest in variable interest entities, which is a filing disclosure "
        "measure of the same kind of claim. Both are reported when they disagree."
    )
    total = group(facts, NONCONTROLLING_TOTAL, valuation_date)
    vie = group(facts, NONCONTROLLING_VIE, valuation_date)
    redeemable = group(facts, NONCONTROLLING_REDEEMABLE, valuation_date)
    sources = [fact for bucket in (total, vie) for fact in bucket.values()]
    notes = []
    conflicts = []
    value = None
    classification = None
    if total:
        concept, fact = sorted(total.items())[0]
        value = fact["value_decimal"]
        classification = "REPORTED_NONCONTROLLING_INTEREST"
        if vie:
            vie_fact = sorted(vie.items())[0][1]
            if vie_fact["value_decimal"] != value:
                conflicts.append(
                    "The reported noncontrolling interest total ("
                    + value
                    + " at "
                    + fact["period_end"]
                    + ") and the noncontrolling interest in variable interest entities ("
                    + vie_fact["value_decimal"]
                    + " at "
                    + vie_fact["period_end"]
                    + ") disagree; both are reported and neither is chosen silently."
                )
    elif vie:
        concept, fact = sorted(vie.items())[0]
        value = fact["value_decimal"]
        classification = "VIE_DISCLOSURE_NONCONTROLLING_INTEREST"
        notes.append(
            "No separate noncontrolling interest line is reported; the noncontrolling interest in "
            "variable interest entities is the reported measure of the same claim and is used as the "
            "claim on consolidated equity attributable to parties other than the parent."
        )
    if redeemable:
        concept, fact = sorted(redeemable.items())[0]
        redeemable_block = {
            "value_decimal": fact["value_decimal"],
            "unit": "USD",
            "evidence_state": "REPORTED",
            "concept": fact["concept"],
            "period_end": fact["period_end"],
            "available_at": fact["available_at"],
            "evidence_digest": fact["evidence_digest"],
            "note": "Reported separate from the total above; verify it is not already included before deducting it.",
        }
    else:
        redeemable_block = {
            "value_decimal": None,
            "unit": "USD",
            "evidence_state": "UNAVAILABLE",
            "concept": None,
            "period_end": None,
            "available_at": None,
            "evidence_digest": None,
            "note": (
                "A redeemable noncontrolling interest subset is not exposed by Company Facts for this "
                "filer. It is not estimated and it is not added to the total, because a redeemable "
                "subset is normally already inside the reported noncontrolling interest or the reported "
                "liability total. Deducting both would double count."
            ),
        }
    if value is None:
        return claim(
            None,
            unit="USD",
            measure="Noncontrolling and redeemable minority interests.",
            state="REVIEWED_ABSENT",
            sources=[],
            selection_rule=rule,
            notes=[
                "No noncontrolling interest is reported in the eligible window.",
                "Candidate concepts reviewed: "
                + ", ".join(NONCONTROLLING_TOTAL + NONCONTROLLING_VIE + NONCONTROLLING_REDEEMABLE)
                + ". A missing concept is not evidence of a zero claim.",
            ],
            extra={
                "classification": None,
                "redeemable_subset": redeemable_block,
                "treatment": "NO_CLAIM_IDENTIFIED",
                "deduction_permitted": False,
                "permitted_for_valuation": True,
                "verified_absent": True,
                "conflicts": [],
            },
        )
    state = "CONFLICTING" if conflicts else "REPORTED"
    return claim(
        value,
        unit="USD",
        measure="Noncontrolling and redeemable minority interests.",
        state=state,
        sources=sources,
        selection_rule=rule,
        notes=notes,
        extra={
            "classification": classification,
            "redeemable_subset": redeemable_block,
            "treatment": "DEDUCT_FROM_CONSOLIDATED_ENTERPRISE_VALUE",
            "treatment_basis": (
                "The enterprise value is built from consolidated financials, so the claim on consolidated "
                "equity held by parties other than the parent is deducted once from enterprise value."
            ),
            "deduction_permitted": not conflicts,
            "permitted_for_valuation": not conflicts,
            "conflicts": conflicts,
        },
    )


def common_shares_claim(facts, valuation_date):
    rule = (
        "Cover-page dei:EntityCommonStockSharesOutstanding when reported; otherwise the "
        "balance-sheet all-class us-gaap:CommonStockSharesOutstanding."
    )
    cover = group(facts, ("EntityCommonStockSharesOutstanding",), valuation_date, unit="shares", taxonomy="dei")
    balance = group(facts, COMMON_SHARES_BALANCE_SHEET, valuation_date, unit="shares")
    chosen, basis, concept, taxonomy = None, None, None, None
    if cover:
        taxonomy, concept = "dei", "EntityCommonStockSharesOutstanding"
        chosen = sorted(cover.items())[0][1]
        basis = "COVER_PAGE_DEI"
    elif balance:
        taxonomy, concept = "us-gaap", "CommonStockSharesOutstanding"
        chosen = sorted(balance.items())[0][1]
        basis = "BALANCE_SHEET_ALL_CLASSES"
    notes = [
        "Per-class counts are not exposed by the Company Facts API; class detail requires the filing "
        "cover page or the XBRL instance and is reported UNAVAILABLE rather than assumed."
    ]
    if basis == "BALANCE_SHEET_ALL_CLASSES":
        notes.append("No cover-page count is available for this filer, so the balance-sheet count is used.")
    if chosen is None:
        return claim(
            None,
            unit="shares",
            measure="Common shares outstanding, point in time.",
            state="UNAVAILABLE",
            sources=[],
            selection_rule=rule,
            notes=notes + ["No common share count concept is reported in the eligible window."],
            extra={"basis": None, "classes": {"evidence_state": "UNAVAILABLE"}, "dated_observations": []},
        )
    dated = observations(facts, concept, valuation_date, unit="shares", taxonomy=taxonomy)
    return claim(
        chosen["value_decimal"],
        unit="shares",
        measure="Common shares outstanding, point in time.",
        state="REPORTED",
        sources=[chosen],
        selection_rule=rule,
        notes=notes,
        extra={
            "basis": basis,
            "classes": {
                "evidence_state": "UNAVAILABLE",
                "note": "Class-level counts are not available from Company Facts.",
            },
            "dated_observations": dated,
            "not_a_weighted_average": True,
        },
    )


def observed_equity_totals(facts, valuation_date):
    found = group(facts, REPORTED_EQUITY_TOTALS, valuation_date)
    return [
        {
            "concept": fact["concept"],
            "value_decimal": fact["value_decimal"],
            "period_end": fact["period_end"],
            "accession": fact["accession"],
            "evidence_digest": fact["evidence_digest"],
        }
        for fact in sorted(found.values(), key=lambda f: f["concept"])
    ]


def build_claims(facts, valuation_date, *, cik=None, preferred_terms=None):
    """Full versioned claims contract for one filer at one valuation date."""
    if not isinstance(facts, dict):
        raise ValueError("COMPANY_FACTS_REQUIRED")
    if not isinstance(valuation_date, date):
        raise ValueError("INVALID_VALUATION_DATE")
    claims = {
        "debt": debt_claim(facts, valuation_date),
        "finance_leases": finance_lease_claim(facts, valuation_date),
        "operating_leases": operating_lease_claim(facts, valuation_date),
        "cash": cash_claim(facts, valuation_date),
        "nonoperating_assets": nonoperating_asset_claim(facts, valuation_date),
        "preferred_equity": preferred_claim(facts, valuation_date, terms=preferred_terms),
        "noncontrolling_interests": noncontrolling_claim(facts, valuation_date),
        "common_shares": common_shares_claim(facts, valuation_date),
    }
    unresolved = []
    for name, value in sorted(claims.items()):
        if value["evidence_state"] not in CERTIFIED_STATES:
            unresolved.append(
                {
                    "claim": name,
                    "evidence_state": value["evidence_state"],
                    "reason": "CLAIM_NOT_CERTIFIED_FOR_BRIDGE",
                }
            )
        if value.get("permitted_for_valuation") is False:
            unresolved.append(
                {
                    "claim": name,
                    "evidence_state": value["evidence_state"],
                    "reason": "CLAIM_CONFLICTS_MUST_BE_RESOLVED",
                }
            )
    deduct = []
    add = []
    missing = []
    verified_absent = []
    for name in DEDUCT_FROM_ENTERPRISE_VALUE + ADD_TO_ENTERPRISE_VALUE:
        value = claims[name]
        if value["value_decimal"] is None:
            if value["evidence_state"] == "REVIEWED_ABSENT":
                # A reviewed-absent claim is a verified zero, not an unknown and not assumed.
                verified_absent.append(name)
            else:
                missing.append(name)
        elif value["evidence_state"] not in CERTIFIED_STATES:
            missing.append(name)
        elif name in DEDUCT_FROM_ENTERPRISE_VALUE:
            deduct.append((name, Decimal(value["value_decimal"])))
        else:
            add.append((name, Decimal(value["value_decimal"])))
    if missing:
        aggregate = {
            "status": "UNAVAILABLE",
            "missing": sorted(missing),
            "verified_absent": sorted(verified_absent),
            "note": "A missing claim is never treated as zero in the bridge.",
        }
    else:
        aggregate = {
            "status": "AVAILABLE",
            "deduct_claims_decimal": format(sum(v for _, v in deduct).normalize(), "f"),
            "add_claims_decimal": format(sum(v for _, v in add).normalize(), "f"),
            "deduct_components": {name: format(v.normalize(), "f") for name, v in sorted(deduct)},
            "add_components": {name: format(v.normalize(), "f") for name, v in sorted(add)},
            "verified_absent": sorted(verified_absent),
            "basis": "Consolidated enterprise value to common equity, point in time.",
        }
    return {
        "schema_version": VERSION,
        "cik": cik,
        "valuation_date": valuation_date.isoformat(),
        "rights": "PUBLIC_SEC_ONLY",
        "claims": claims,
        "aggregate_claims": aggregate,
        "reconciliation": {
            "deduct_from_enterprise_value": list(DEDUCT_FROM_ENTERPRISE_VALUE),
            "add_to_enterprise_value": list(ADD_TO_ENTERPRISE_VALUE),
            "separately_classified": {
                "operating_leases": (
                    "Reported as a claim and excluded from the bridge: EBIT is after operating lease cost."
                )
            },
            "excluded_by_policy": {
                "long_term_and_equity_investments": "Not treated as nonoperating cash equivalents."
            },
            "reported_equity_totals": observed_equity_totals(facts, valuation_date),
            "double_count_guards": [
                "A filer-stated current-debt total is not added to a component that may already contain it.",
                "Marketable securities are included once, not once per alternative concept.",
                "A redeemable minority subset is never added to the total it belongs to.",
                "Preferred carrying value, liquidation preference and conversion value are alternatives and are never summed.",
            ],
        },
        "evidence_states": list(EVIDENCE_STATES),
        "unresolved": unresolved,
        "limits": {
            "max_age_days": MAX_AGE_DAYS,
            "dimensional_and_duration_facts_excluded": True,
            "class_level_share_counts_available": False,
        },
        "certification": "FILING_BACKED_CLAIMS;NOT_A_MARKET_OR_ACCOUNTING_ATTESTATION",
    }
