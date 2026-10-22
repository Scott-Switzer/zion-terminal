"""Versioned source-preserving SEC histories. No provider calls or licensed data."""

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, localcontext

from temporal_core import normalize_legacy_date_only, parse_instant

VERSION = "sec-financial-history-v1"
REGISTRY = {
    "revenue": {
        "unit": "USD",
        "kind": "flow",
        "concepts": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ],
        "definition": "Reported revenue in the exact native concept; no assessed-tax-including substitution.",
    },
    "operating_income": {
        "unit": "USD",
        "kind": "flow",
        "concepts": ["OperatingIncomeLoss"],
        "definition": "Reported operating income or loss.",
    },
    "net_income": {
        "unit": "USD",
        "kind": "flow",
        "concepts": ["NetIncomeLoss"],
        "definition": "Consolidated net income/loss; not net income available to common.",
    },
    "operating_cash_flow": {
        "unit": "USD",
        "kind": "flow",
        "concepts": ["NetCashProvidedByUsedInOperatingActivities"],
        "definition": "Net cash from operating activities.",
    },
    "capital_expenditures": {
        "unit": "USD",
        "kind": "flow",
        "concepts": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "definition": "Reported positive PP&E cash payments; excludes acquisitions/intangibles and other investment.",
    },
    "cash": {
        "unit": "USD",
        "kind": "instant",
        "concepts": ["CashAndCashEquivalentsAtCarryingValue"],
        "definition": "Cash and cash equivalents; excludes restricted cash and marketable securities.",
    },
    "total_debt": {
        "unit": "USD",
        "kind": "instant",
        "concepts": [],
        "definition": "Reported carrying amounts of borrowings plus capital/finance leases, using all explicit nonoverlapping same-date components. Excludes operating leases. Missing components never become zero.",
    },
    "diluted_shares": {
        "unit": "shares",
        "kind": "weighted",
        "concepts": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
        "definition": "Reported period weighted-average diluted shares; never sum or subtract weighted averages.",
    },
    "free_cash_flow": {
        "unit": "USD",
        "kind": "flow",
        "concepts": [],
        "definition": "Operating cash flow minus positive PP&E payments, with exact matching periods/currency.",
    },
}
REVENUE_CONCEPTS = {
    "0000909832": ["Revenues"],
    # Keep this registry in step with sec_engine.py: Walmart's total Revenues is
    # the statement total; ASC-606 contract revenue is a narrower definition.
    "0000104169": ["Revenues"],
}

DEBT = {
    "0000320193": [
        "CommercialPaper",
        "OtherShortTermBorrowings",
        "LongTermDebtCurrent",
        "LongTermDebtNoncurrent",
        "FinanceLeaseLiabilityCurrent",
        "FinanceLeaseLiabilityNoncurrent",
    ],
    "0000909832": [
        "ShortTermBorrowings",
        "LongTermDebtCurrent",
        "LongTermDebtNoncurrent",
        "FinanceLeaseLiabilityCurrent",
        "FinanceLeaseLiabilityNoncurrent",
    ],
    "0001045810": ["DebtCurrent", "LongTermDebtNoncurrent", "CapitalLeaseObligationsNoncurrent"],
    "0001326801": ["DebtCurrent", "LongTermDebtNoncurrent", "CapitalLeaseObligationsNoncurrent"],
    "0001652044": ["DebtCurrent", "LongTermDebtNoncurrent", "CapitalLeaseObligationsNoncurrent"],
}

DEBT_QUARANTINED = {"0000320193", "0000909832"}


class HistoryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def normalize_sources(company, cik, member_sha256):
    if str(company.get("cik")).zfill(10) != cik:
        raise HistoryError("SOURCE_IDENTITY_MISMATCH")
    observations = []
    rejected_contexts = []
    rejections = {}
    seen = set()
    mapping = {tag: m for m, r in REGISTRY.items() for tag in r["concepts"]}
    if cik in REVENUE_CONCEPTS:
        mapping = {tag: m for tag, m in mapping.items() if m != "revenue"}
        mapping.update({tag: "revenue" for tag in REVENUE_CONCEPTS[cik]})
    mapping.update({tag: "debt_component:" + tag for tag in DEBT.get(cik, [])})
    for tag, metric in mapping.items():
        expected = REGISTRY.get(metric, {"unit": "USD"})["unit"]
        for unit, rows in (
            company.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).items()
        ):
            for row in rows:
                reason = "SOURCE_VALUE_FIELD_MISSING" if "val" not in row else None
                if unit != expected:
                    reason = "UNSUPPORTED_NATIVE_UNIT"
                elif row.get("form") not in ["10-K", "10-K/A", "10-Q", "10-Q/A"]:
                    reason = "UNSUPPORTED_FORM"
                try:
                    end = date.fromisoformat(row["end"])
                    start = date.fromisoformat(row["start"]) if row.get("start") else None
                    available = normalize_legacy_date_only(row["filed"]).iso_utc
                    if start and start > end:
                        raise ValueError()
                    if not row.get("accn"):
                        raise ValueError()
                    value = None if row.get("val") is None else Decimal(str(row["val"]))
                    if value is not None and not value.is_finite():
                        reason = "NONFINITE_SOURCE_VALUE"
                except (ValueError, TypeError, KeyError):
                    reason = "INCOMPLETE_SOURCE_CONTEXT"
                if reason:
                    if (
                        reason in ("NONFINITE_SOURCE_VALUE", "SOURCE_VALUE_FIELD_MISSING")
                        and unit == expected
                    ):
                        rejected_contexts.append(
                            {
                                "metric": metric,
                                "concept": "us-gaap:" + tag,
                                "period_start": start.isoformat() if start else None,
                                "period_end": end.isoformat(),
                                "available_at": available,
                                "reason": reason,
                            }
                        )
                    rejections[reason] = rejections.get(reason, 0) + 1
                    continue
                original = {
                    k: (format(v, "f") if isinstance(v, Decimal) else v) for k, v in row.items()
                }
                sid = hashlib.sha256(
                    encoded({"cik": cik, "concept": tag, "unit": unit, "source_record": original})
                ).hexdigest()
                if sid in seen:
                    continue
                seen.add(sid)
                observations.append(
                    {
                        "cik": cik,
                        "metric": metric,
                        "concept": "us-gaap:" + tag,
                        "unit": unit,
                        "currency": "USD" if unit == "USD" else None,
                        "period_start": start.isoformat() if start else None,
                        "period_end": end.isoformat(),
                        "available_at": available,
                        "accession": row["accn"],
                        "form": row["form"],
                        "source_fy": row.get("fy"),
                        "source_fp": row.get("fp"),
                        "value_decimal": format(value, "f") if value is not None else None,
                        "source_observation_id": sid,
                        "member_sha256": member_sha256,
                        "availability_policy": "DATE_ONLY_NEXT_DAY_ET_V1",
                        "source_record": original,
                    }
                )
    return {
        "schema_version": VERSION,
        "cik": cik,
        "label": company.get("entityName"),
        "rights": "PUBLIC_SEC_ONLY",
        "registry_version": VERSION,
        "member_sha256": member_sha256,
        "observations": sorted(
            observations,
            key=lambda x: (
                x["metric"],
                x["period_end"],
                x["available_at"],
                x["source_observation_id"],
            ),
        ),
        "rejections": rejections,
        "rejected_contexts": rejected_contexts,
        "concept_definitions": {
            "us-gaap:" + tag: {
                field: company.get("facts", {}).get("us-gaap", {}).get(tag, {}).get(field)
                for field in ("label", "description")
            }
            for tag in mapping
        },
    }


def number(value):
    if value is None:
        raise HistoryError("SOURCE_NULL")
    n = Decimal(value)
    if not n.is_finite():
        raise HistoryError("NONFINITE_SOURCE_VALUE")
    return n


def calculate(parts, operation, metric, start, end):
    if any(p["unit"] != parts[0]["unit"] or p["currency"] != parts[0]["currency"] for p in parts):
        raise HistoryError("INCOMPATIBLE_COMPONENT_UNITS")
    with localcontext() as ctx:
        vals = [number(p["value_decimal"]) for p in parts]
        if any(len(v.as_tuple().digits) > 80 for v in vals):
            raise HistoryError("DECIMAL_PRECISION_BUDGET")
        ctx.prec = (
            max(1, max(v.adjusted() for v in vals) - min(v.as_tuple().exponent for v in vals) + 1)
            + len(vals)
            + 4
        )
        if ctx.prec > 512:
            raise HistoryError("DECIMAL_PRECISION_BUDGET")
        value = sum(vals, Decimal(0)) if operation == "SUM" else vals[0] - sum(vals[1:], Decimal(0))
    terms = {}
    for index, part in enumerate(parts):
        sign = 1 if operation == "SUM" or index == 0 else -1
        for term in part["source_terms"]:
            sid = term["source_observation_id"]
            terms[sid] = terms.get(sid, 0) + sign * term["coefficient"]
    return {
        "source_terms": [
            {"source_observation_id": sid, "coefficient": coef}
            for sid, coef in sorted(terms.items())
            if coef
        ],
        "metric": metric,
        "period_start": start,
        "period_end": end,
        "value_decimal": format(value, "f"),
        "unit": parts[0]["unit"],
        "currency": parts[0]["currency"],
        "available_at": max(p["available_at"] for p in parts),
        "concept": parts[0]["concept"]
        if all(p["concept"] == parts[0]["concept"] for p in parts)
        else "DERIVED_MULTIPLE_EXPLICIT_CONCEPTS",
        "record_kind": "DERIVED",
        "formula": operation,
        "source_observation_ids": sorted({i for p in parts for i in p["source_observation_ids"]}),
        "source_components": [
            {
                k: p[k]
                for k in (
                    "metric",
                    "concept",
                    "period_start",
                    "period_end",
                    "value_decimal",
                    "unit",
                    "currency",
                    "available_at",
                    "source_observation_ids",
                )
            }
            | {"formula": p.get("formula", "SOURCE_REPORTED")}
            for p in parts
        ],
        "registry_version": VERSION,
        "availability_policy": "MAX_COMPONENT_AVAILABILITY",
        "validation_scope": "SOURCE_RECONCILED_RESEARCH_NOT_FULL_FINANCIAL_CERTIFICATION",
    }


def history(data, *, metrics, frequency, date_from, date_to, as_of):
    if data.get("rights") != "PUBLIC_SEC_ONLY" or data.get("schema_version") != VERSION:
        raise HistoryError("UNAPPROVED_SOURCE")
    if (
        not metrics
        or len(metrics) > 9
        or len(set(metrics)) != len(metrics)
        or any(m not in REGISTRY for m in metrics)
    ):
        raise HistoryError("INVALID_METRIC")
    if frequency not in ["annual", "quarterly", "ttm"]:
        raise HistoryError("INVALID_FREQUENCY")
    try:
        lo, hi = date.fromisoformat(date_from), date.fromisoformat(date_to)
        cutoff = parse_instant(as_of).iso_utc
        if lo > hi or (hi - lo).days > 12 * 366:
            raise ValueError()
    except (ValueError, TypeError):
        raise HistoryError("INVALID_DATE_RANGE") from None
    quarter_max_days = 120 if data["cik"] == "0000909832" else 105
    groups = {}
    gaps = []
    anchors = {"annual": set(), "quarterly": set()}
    for row in data["observations"]:
        if row["available_at"] > cutoff or row["period_end"] > date_to:
            continue
        start = row["period_start"]
        end = row["period_end"]
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if start else None
        if row["metric"] in REGISTRY and start:
            if 330 <= days <= 380 and row["form"] in ("10-K", "10-K/A"):
                anchors["annual"].add((start, end))
            if 75 <= days <= quarter_max_days:
                anchors["quarterly"].add((start, end))
        groups.setdefault((row["metric"], row["concept"], start, end), []).append(row)
    selected = {}
    for key, rows in groups.items():
        latest = max(r["available_at"] for r in rows)
        blockers = [
            r
            for r in data.get("rejected_contexts", [])
            if r["metric"] == key[0]
            and r["concept"] == key[1]
            and r["period_start"] == key[2]
            and r["period_end"] == key[3]
            and latest <= r["available_at"] <= cutoff
        ]
        if blockers:
            gaps.append(
                {
                    "metric": key[0],
                    "period_start": key[2],
                    "period_end": key[3],
                    "reason": "LATEST_SOURCE_CONTEXT_INVALID_NO_OLDER_FALLBACK",
                }
            )
            continue
        versions = [r for r in rows if r["available_at"] == latest]
        values = {r["value_decimal"] for r in versions}
        if len(values) != 1:
            gaps.append(
                {
                    "metric": key[0],
                    "period_start": key[2],
                    "period_end": key[3],
                    "reason": "CONFLICTING_SOURCE_VALUES",
                }
            )
            continue
        chosen = min(versions, key=lambda x: x["source_observation_id"])
        selected[key] = {
            k: chosen[k]
            for k in [
                "metric",
                "concept",
                "unit",
                "currency",
                "period_start",
                "period_end",
                "value_decimal",
                "available_at",
                "accession",
                "form",
                "member_sha256",
                "availability_policy",
            ]
        }
        selected[key].update(
            source_terms=[
                {
                    "source_observation_id": min(r["source_observation_id"] for r in versions),
                    "coefficient": 1,
                }
            ],
            record_kind="NORMALIZED_SOURCE_OBSERVATION",
            source_observation_ids=sorted({r["source_observation_id"] for r in versions}),
            source_record_ids=sorted({r["source_observation_id"] for r in rows}),
            registry_version=VERSION,
            validation_scope="SOURCE_RECONCILED_RESEARCH_NOT_FULL_FINANCIAL_CERTIFICATION",
        )
    period_values = {"annual": {}, "quarterly": {}}

    # Select exact native concepts. Conflicting alternative definitions stay unavailable.
    def insert(freq, key, item):
        existing = period_values[freq].get(key)
        if existing is False:
            return
        if existing and (existing["value_decimal"] != item["value_decimal"]):
            period_values[freq][key] = False
            gaps.append(
                {
                    "metric": key[0],
                    "period_start": key[1],
                    "period_end": key[2],
                    "reason": "AMBIGUOUS_CONCEPT_OR_DERIVATION",
                }
            )
        elif not existing:
            period_values[freq][key] = item
        elif existing["concept"] != item["concept"] and key[0] in REGISTRY:
            concepts = (
                REVENUE_CONCEPTS.get(data["cik"], REGISTRY[key[0]]["concepts"])
                if key[0] == "revenue"
                else REGISTRY[key[0]]["concepts"]
            )
            if (
                item["concept"].removeprefix("us-gaap:") in concepts
                and existing["concept"].removeprefix("us-gaap:") in concepts
            ):
                if concepts.index(item["concept"].removeprefix("us-gaap:")) < concepts.index(
                    existing["concept"].removeprefix("us-gaap:")
                ):
                    period_values[freq][key] = item

    for (_, _, start, end), item in selected.items():
        if not start:
            continue
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        if item["value_decimal"] is None:
            gaps.append(
                {
                    "metric": item["metric"],
                    "period_start": start,
                    "period_end": end,
                    "reason": "SOURCE_NULL",
                }
            )
            continue
        if (
            item["metric"] in ("capital_expenditures", "diluted_shares")
            and number(item["value_decimal"]) < 0
        ):
            gaps.append(
                {
                    "metric": item["metric"],
                    "period_start": start,
                    "period_end": end,
                    "reason": "NEGATIVE_VALUE_INVALID_FOR_METRIC_DEFINITION",
                }
            )
            continue
        if 330 <= days <= 380 and item["form"] in ("10-K", "10-K/A"):
            insert("annual", (item["metric"], start, end), item)
        if 75 <= days <= quarter_max_days:
            insert("quarterly", (item["metric"], start, end), item)
        if (
            item["metric"] not in REGISTRY
            or REGISTRY[item["metric"]]["kind"] != "flow"
            or days < 150
        ):
            continue
        prior = [
            p
            for (m, c, s, e), p in selected.items()
            if m == item["metric"]
            and c == item["concept"]
            and s == start
            and e < end
            and 75 <= (date.fromisoformat(end) - date.fromisoformat(e)).days <= quarter_max_days
            and p["value_decimal"] is not None
            and (item["metric"] != "capital_expenditures" or number(p["value_decimal"]) >= 0)
        ]
        if len(prior) == 1:
            previous = prior[0]
            qstart = (date.fromisoformat(previous["period_end"]) + timedelta(days=1)).isoformat()
            if (
                75
                <= (date.fromisoformat(previous["period_end"]) - date.fromisoformat(start)).days + 1
                <= 300
            ):
                derived = calculate([item, previous], "SUBTRACT", item["metric"], qstart, end)
                if (
                    item["metric"] == "capital_expenditures"
                    and number(derived["value_decimal"]) < 0
                ):
                    gaps.append(
                        {
                            "metric": item["metric"],
                            "period_start": qstart,
                            "period_end": end,
                            "reason": "NEGATIVE_DERIVED_PPE_PAYMENT",
                        }
                    )
                    continue
                key = (item["metric"], qstart, end)
                # A reported quarter wins only when it exactly reconciles to the difference.
                existing = period_values["quarterly"].get(key)
                if existing and existing["value_decimal"] == derived["value_decimal"]:
                    continue
                insert("quarterly", key, derived)
                if item["metric"] in REGISTRY:
                    anchors["quarterly"].add((qstart, end))
    for freq in ["annual", "quarterly"]:
        for start, end in sorted(anchors[freq]):
            for metric in ["cash"]:
                candidates = [
                    p
                    for (m, c, s, e), p in selected.items()
                    if m == metric and s is None and e == end
                ]
                if len(candidates) == 1 and candidates[0]["value_decimal"] is not None:
                    period_values[freq][(metric, None, end)] = candidates[0]
            if data["cik"] in DEBT_QUARANTINED:
                gaps.append(
                    {
                        "metric": "total_debt",
                        "period_start": None,
                        "period_end": end,
                        "reason": "COMPANY_DEBT_COMPONENT_NONOVERLAP_NOT_FILINGLEVEL_CONFIRMED",
                    }
                )
            parts = []
            for tag in DEBT.get(data["cik"], []):
                candidates = [
                    p
                    for (m, c, s, e), p in selected.items()
                    if m == "debt_component:" + tag
                    and s is None
                    and e == end
                    and p["value_decimal"] is not None
                    and (
                        item["metric"] != "capital_expenditures" or number(p["value_decimal"]) >= 0
                    )
                ]
                if len(candidates) != 1 or number(candidates[0]["value_decimal"]) < 0:
                    break
                parts.append(candidates[0])
            else:
                if parts and data["cik"] not in DEBT_QUARANTINED:
                    period_values[freq][("total_debt", None, end)] = calculate(
                        parts, "SUM", "total_debt", None, end
                    )
            cfo = period_values[freq].get(("operating_cash_flow", start, end))
            capex = period_values[freq].get(("capital_expenditures", start, end))
            if cfo and capex and number(capex["value_decimal"]) >= 0:
                period_values[freq][("free_cash_flow", start, end)] = calculate(
                    [cfo, capex], "SUBTRACT", "free_cash_flow", start, end
                )
    output = []
    if frequency == "ttm":
        for metric in metrics:
            if REGISTRY[metric]["kind"] != "flow":
                gaps.append(
                    {
                        "metric": metric,
                        "period_start": None,
                        "period_end": None,
                        "reason": "TTM_NOT_DEFINED_FOR_NONADDITIVE_METRIC",
                    }
                )
                continue
            quarters = sorted(
                [v for (m, s, e), v in period_values["quarterly"].items() if m == metric and v],
                key=lambda x: x["period_end"],
            )
            for i in range(len(quarters)):
                last = quarters[max(0, i - 3) : i + 1]
                if len(last) != 4:
                    continue
                if any(
                    date.fromisoformat(b["period_start"])
                    != date.fromisoformat(a["period_end"]) + timedelta(days=1)
                    for a, b in zip(last, last[1:])
                ):
                    continue
                if len({x["concept"] for x in last}) != 1:
                    continue
                span = (
                    date.fromisoformat(last[-1]["period_end"])
                    - date.fromisoformat(last[0]["period_start"])
                ).days + 1
                if not 330 <= span <= 380:
                    continue
                output.append(
                    calculate(last, "SUM", metric, last[0]["period_start"], last[-1]["period_end"])
                )
    else:
        output = [
            {**v, "frequency": frequency}
            for (m, s, e), v in period_values[frequency].items()
            if v and m in metrics
        ]
    output = [
        {**x, "frequency": frequency} for x in output if date_from <= x["period_end"] <= date_to
    ]
    output.sort(key=lambda x: (x["metric"], x["period_end"], x["period_start"] or ""))
    for metric in metrics:
        ends = {x["period_end"] for x in output if x["metric"] == metric}
        for start, end in sorted(anchors["quarterly" if frequency == "ttm" else frequency]):
            if date_from <= end <= date_to and end not in ends:
                gaps.append(
                    {
                        "metric": metric,
                        "period_start": start,
                        "period_end": end,
                        "reason": "REQUIRED_SOURCE_CONTEXT_OR_DERIVATION_UNAVAILABLE",
                    }
                )
        if not ends:
            gaps.append(
                {
                    "metric": metric,
                    "period_start": date_from,
                    "period_end": date_to,
                    "reason": "NO_VALID_OBSERVATIONS_IN_REQUEST",
                }
            )
    gaps = [
        g
        for g in gaps
        if g["metric"] in metrics
        and (g["period_end"] is None or date_from <= g["period_end"] <= date_to)
    ]
    return {
        "schema_version": VERSION,
        "cik": data["cik"],
        "registry_version": VERSION,
        "as_of": cutoff,
        "frequency": frequency,
        "observations": output,
        "coverage_gaps": gaps,
        "coverage_qualification": "PERMITTED_STORED_SEC_SOURCE_ONLY;NO_IMPUTATION;NO_FULL_FINANCIAL_CERTIFICATION",
        "revision_policy": "FILTER_AVAILABILITY_THEN_LATEST_SOURCE_FILING_DATE;CONFLICTS_REJECTED;DERIVATIONS_USE_LATEST_COMPONENT_VINTAGES",
        "member_sha256": data["member_sha256"],
    }
