"""Versioned source-preserving SEC histories. No provider calls or licensed data."""

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, localcontext

from temporal_core import normalize_legacy_date_only, parse_instant

VERSION = "sec-financial-engine-v2"
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
REVENUE_CONCEPTS = {"0000909832": ["Revenues"]}

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


def statement_policy(company):
    tags = company.get("facts", {}).get("us-gaap", {})
    evidence = sorted(
        set(tags)
        & {
            "InterestIncomeExpenseAfterProvisionForLoanLosses",
            "InterestAndDividendIncomeOperating",
            "PremiumsEarnedNet",
            "InsurancePremiumsNet",
            "PolicyholderBenefitsAndClaimsIncurredNet",
            "LoansAndLeasesReceivableNetReportedAmount",
        }
    )
    cik = str(company.get("cik", "")).zfill(10)
    quarantined = {"total_debt": "COMPONENT_NONOVERLAP_NOT_CONFIRMED"}
    if cik in DEBT and cik not in DEBT_QUARANTINED:
        quarantined.pop("total_debt")
    if evidence:
        quarantined.update(
            {
                m: "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED"
                for m in (
                    "revenue",
                    "operating_income",
                    "capital_expenditures",
                    "free_cash_flow",
                    "total_debt",
                )
            }
        )
    return {
        "family": "financial_statement_evidence" if evidence else "general_us_gaap_unclassified",
        "classification_evidence": evidence,
        "classification_qualification": "TAXONOMY_STATEMENT_FAMILY_NOT_CERTIFIED_INDUSTRY_SIC",
        "quarantined_metrics": quarantined,
        "debt_components": DEBT.get(cik, []),
        "revenue_concepts": REVENUE_CONCEPTS.get(cik, REGISTRY["revenue"]["concepts"]),
        "quarter_max_days": 105,
        "statement_types": {
            m: (
                "balance_sheet"
                if r["kind"] == "instant"
                else "cash_flow"
                if m in ("operating_cash_flow", "capital_expenditures", "free_cash_flow")
                else "income_statement"
            )
            for m, r in REGISTRY.items()
        },
    }


def normalize_sources(company, cik, member_sha256):
    policy = statement_policy(company)
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
    mapping.update({tag: "debt_component:" + tag for tag in policy["debt_components"]})
    mapping.update(
        {tag: "balance_component:" + tag for tag in ("Assets", "LiabilitiesAndStockholdersEquity")}
    )
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
                    if end > date.fromisoformat(row["filed"]):
                        reason = "PERIOD_END_AFTER_SOURCE_FILING_DATE"
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
    policy["long_quarter_contexts"] = infer_calendar(observations)
    if policy["long_quarter_contexts"]:
        policy["quarter_max_days"] = 120
    return {
        "schema_version": VERSION,
        "policy": policy,
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
        if lo > hi or (hi - lo).days > 100 * 366:
            raise ValueError()
    except (ValueError, TypeError):
        raise HistoryError("INVALID_DATE_RANGE") from None
    quarter_max_days = data.get("policy", {}).get("quarter_max_days", 105)
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
            if quarter_ok(data, start, end, days, cutoff):
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
        if quarter_ok(data, start, end, days, cutoff):
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
                if not quarter_ok(
                    data,
                    qstart,
                    end,
                    (date.fromisoformat(end) - date.fromisoformat(qstart)).days + 1,
                    cutoff,
                ):
                    continue
                key = (item["metric"], qstart, end)
                # A reported quarter wins only when it exactly reconciles to the difference.
                existing = period_values["quarterly"].get(key)
                if existing and existing["value_decimal"] == derived["value_decimal"]:
                    continue
                insert("quarterly", key, derived)
                if item["metric"] in REGISTRY:
                    anchors["quarterly"].add((qstart, end))
    # Different starts ending on the same fiscal date are different economic contexts.
    # Never silently select one or double-count them in a trailing window.
    ambiguous_ids = set()
    for freq, values in period_values.items():
        contexts = {}
        for (metric, start, end), value in values.items():
            if value and start:
                contexts.setdefault((metric, end), []).append((metric, start, end))
        for (metric, end), keys in contexts.items():
            if len(keys) > 1:
                for key in keys:
                    ambiguous_ids.update(values[key]["source_observation_ids"])
                    values[key] = False
                gaps.append(
                    {
                        "metric": metric,
                        "period_start": None,
                        "period_end": end,
                        "reason": "OVERLAPPING_FISCAL_CONTEXTS",
                    }
                )
    for freq, values in period_values.items():
        for key, value in values.items():
            if (
                value
                and value.get("record_kind") == "DERIVED"
                and ambiguous_ids.intersection(value["source_observation_ids"])
            ):
                values[key] = False
                gaps.append(
                    {
                        "metric": key[0],
                        "period_start": key[1],
                        "period_end": key[2],
                        "reason": "DERIVATION_USES_AMBIGUOUS_FISCAL_CONTEXT",
                    }
                )
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
            for tag in data.get("policy", {}).get("debt_components", []):
                candidates = [
                    p
                    for (m, c, s, e), p in selected.items()
                    if m == "debt_component:" + tag
                    and s is None
                    and e == end
                    and p["value_decimal"] is not None
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
    quarantined = data.get("policy", {}).get("quarantined_metrics", {})
    for metric in metrics:
        if metric in quarantined:
            gaps.append(
                {
                    "metric": metric,
                    "period_start": date_from,
                    "period_end": date_to,
                    "reason": quarantined[metric],
                }
            )
    output = [
        {**x, "frequency": frequency}
        for x in output
        if date_from <= x["period_end"] <= date_to and x["metric"] not in quarantined
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
        "mapping_policy": data.get("policy", {}),
        "as_of": cutoff,
        "frequency": frequency,
        "observations": output,
        "coverage_gaps": gaps,
        "coverage_qualification": "PERMITTED_STORED_SEC_SOURCE_ONLY;NO_IMPUTATION;NO_FULL_FINANCIAL_CERTIFICATION",
        "revision_policy": "FILTER_AVAILABILITY_THEN_LATEST_SOURCE_FILING_DATE;CONFLICTS_REJECTED;DERIVATIONS_USE_LATEST_COMPONENT_VINTAGES",
        "member_sha256": data["member_sha256"],
    }


def balance_checks(data, as_of, date_from, date_to):
    """Compare explicitly reported equal-scope balance totals; never solve missing values."""
    cutoff = parse_instant(as_of).iso_utc
    grouped = {}
    for row in data["observations"]:
        if (
            row["metric"].startswith("balance_component:")
            and not row["period_start"]
            and date_from <= row["period_end"] <= date_to
            and row["available_at"] <= cutoff
        ):
            grouped.setdefault((row["period_end"], row["concept"]), []).append(row)
    chosen = {}
    for key, rows in grouped.items():
        latest = max(r["available_at"] for r in rows)
        versions = [r for r in rows if r["available_at"] == latest]
        invalid = any(
            r["metric"] == versions[0]["metric"]
            and r["period_end"] == key[0]
            and r["period_start"] is None
            and latest <= r["available_at"] <= cutoff
            for r in data.get("rejected_contexts", [])
        )
        values = {r["value_decimal"] for r in versions}
        if not invalid and len(values) == 1 and None not in values:
            chosen[key] = min(versions, key=lambda r: r["source_observation_id"])
    checks = []
    for end in sorted({k[0] for k in grouped}):
        left = chosen.get((end, "us-gaap:Assets"))
        right = chosen.get((end, "us-gaap:LiabilitiesAndStockholdersEquity"))
        if not left or not right:
            checks.append(
                {
                    "period_end": end,
                    "status": "UNAVAILABLE",
                    "reason": "EXACT_SCOPE_BALANCE_TOTAL_MISSING_OR_AMBIGUOUS",
                    "source_observation_ids": [],
                }
            )
            continue
        difference = calculate(
            [
                left
                | {
                    "source_terms": [
                        {"source_observation_id": left["source_observation_id"], "coefficient": 1}
                    ],
                    "source_observation_ids": [left["source_observation_id"]],
                },
                right
                | {
                    "source_terms": [
                        {"source_observation_id": right["source_observation_id"], "coefficient": 1}
                    ],
                    "source_observation_ids": [right["source_observation_id"]],
                },
            ],
            "SUBTRACT",
            "balance_identity",
            None,
            end,
        )
        checks.append(
            {
                "period_end": end,
                "status": "PASS" if number(difference["value_decimal"]) == 0 else "FAIL",
                "identity": "Assets = LiabilitiesAndStockholdersEquity",
                "difference_decimal": difference["value_decimal"],
                "source_observation_ids": difference["source_observation_ids"],
                "available_at": difference["available_at"],
                "qualification": "REPORTED_TOTAL_COMPARISON_ONLY;NO_IMPUTATION_OR_CERTIFICATION",
            }
        )
    return checks


def infer_calendar(observations):
    """Long quarters need four contiguous intervals inside a reported annual context."""
    from itertools import combinations

    contexts = {}
    for row in observations:
        if row["period_start"] and row["metric"] in REGISTRY:
            key = (row["concept"], row["period_start"], row["period_end"])
            contexts[key] = min(contexts.get(key, row["available_at"]), row["available_at"])
    allowed = {}
    for row in observations:
        start, end = row["period_start"], row["period_end"]
        if not start or row["form"] not in ("10-K", "10-K/A"):
            continue
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        if not 330 <= days <= 380:
            continue
        concept = row["concept"]
        inner = sorted(e for c, s, e in contexts if c == concept and s == start and start < e < end)
        for trio in combinations(inner, 3):
            boundaries = [(date.fromisoformat(start) - timedelta(days=1)).isoformat(), *trio, end]
            spans = [
                (date.fromisoformat(b) - date.fromisoformat(a)).days
                for a, b in zip(boundaries, boundaries[1:])
            ]
            if not all(75 <= n <= 120 for n in spans):
                continue
            availability = max(contexts[(concept, start, e)] for e in (*trio, end))
            for a, b, n in zip(boundaries, boundaries[1:], spans):
                if n > 105:
                    qs = (date.fromisoformat(a) + timedelta(days=1)).isoformat()
                    allowed[(qs, b)] = min(allowed.get((qs, b), availability), availability)
    return [
        {
            "period_start": s,
            "period_end": e,
            "available_at": a,
            "evidence": "FOUR_CONTIGUOUS_NATIVE_CUMULATIVE_CONTEXTS_INSIDE_ANNUAL",
        }
        for (s, e), a in sorted(allowed.items())
    ]


def quarter_ok(data, start, end, days, cutoff):
    return 75 <= days <= 105 or any(
        c["period_start"] == start and c["period_end"] == end and c["available_at"] <= cutoff
        for c in data.get("policy", {}).get("long_quarter_contexts", [])
    )
