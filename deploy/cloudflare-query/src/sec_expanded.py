"""Bounded SEC-native normalization; concepts and availability stay explicit."""

import zlib
from datetime import date
from decimal import Decimal

from private_financials import PrivateFinancialsError, decimal_value
from temporal_core import normalize_legacy_date_only, parse_instant

MAX_MEMBER = 4 * 1048576
MAX_COMPRESSED = 2 * 1048576
VERSION = "zion-sec-expanded-v1"
# Native units and economic definitions are distinct; no FX or subtotal substitution.
METRICS = {
    "revenue": (
        "USD",
        "duration",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ),
    ),
    "operating_income": ("USD", "duration", ("OperatingIncomeLoss",)),
    "net_income": ("USD", "duration", ("NetIncomeLoss",)),
    "net_income_common": ("USD", "duration", ("NetIncomeLossAvailableToCommonStockholdersBasic",)),
    "cost_of_revenue": ("USD", "duration", ("CostOfRevenue", "CostOfGoodsAndServicesSold")),
    "gross_profit": ("USD", "duration", ("GrossProfit",)),
    "eps_basic": ("USD/shares", "duration", ("EarningsPerShareBasic",)),
    "eps_diluted": ("USD/shares", "duration", ("EarningsPerShareDiluted",)),
    "operating_cash_flow": ("USD", "duration", ("NetCashProvidedByUsedInOperatingActivities",)),
    "investing_cash_flow": ("USD", "duration", ("NetCashProvidedByUsedInInvestingActivities",)),
    "financing_cash_flow": ("USD", "duration", ("NetCashProvidedByUsedInFinancingActivities",)),
    "capex": ("USD", "duration", ("PaymentsToAcquirePropertyPlantAndEquipment",)),
    "depreciation_amortization": (
        "USD",
        "duration",
        ("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"),
    ),
    "cash": ("USD", "instant", ("CashAndCashEquivalentsAtCarryingValue",)),
    "total_assets": ("USD", "instant", ("Assets",)),
    "total_liabilities": ("USD", "instant", ("Liabilities",)),
    "shareholders_equity": ("USD", "instant", ("StockholdersEquity",)),
    "debt_current": ("USD", "instant", ("LongTermDebtCurrent",)),
    "debt_noncurrent": ("USD", "instant", ("LongTermDebtNoncurrent",)),
    "diluted_weighted_average_shares": (
        "shares",
        "duration",
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    ),
}


def reject(code, message, status=422):
    raise PrivateFinancialsError(code, message, status)


def decode_member(compressed: bytes, entry: dict) -> bytes:
    if len(compressed) > MAX_COMPRESSED or not 0 <= entry["uncompressed_bytes"] <= MAX_MEMBER:
        reject("SOURCE_MEMBER_TOO_LARGE", "issuer source exceeds the bounded serving budget")
    if entry["compression"] == 8:
        decoder = zlib.decompressobj(-15)
        raw = decoder.decompress(compressed, MAX_MEMBER + 1)
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            reject("SOURCE_INTEGRITY_FAILURE", "invalid bounded source member", 503)
    elif entry["compression"] == 0:
        raw = compressed
    else:
        reject("SOURCE_INTEGRITY_FAILURE", "unsupported source compression", 503)
    if (
        len(raw) != entry["uncompressed_bytes"]
        or len(raw) > MAX_MEMBER
        or zlib.crc32(raw) != entry["crc"]
    ):
        reject("SOURCE_INTEGRITY_FAILURE", "source member integrity mismatch", 503)
    return raw


def normalize_companyfacts(
    company: dict, *, cik: str, metrics: list[str], period: str, lookback: int, as_of: str
) -> dict:
    if str(company.get("cik", "")).zfill(10) != cik:
        reject("SOURCE_IDENTITY_MISMATCH", "stored issuer does not match requested CIK", 503)
    cutoff = parse_instant(as_of).epoch_ns
    facts = company.get("facts", {}).get("us-gaap", {})
    if not isinstance(facts, dict):
        reject("SOURCE_SCHEMA_INVALID", "issuer financial facts are malformed", 503)
    duration_bounds = (330, 380) if period == "annual" else (75, 105)

    def candidates(metric):
        unit, kind, tags = METRICS[metric]
        found = []
        for priority, tag in enumerate(tags):
            units = facts.get(tag, {}).get("units", {})
            for row in units.get(unit, []):
                if not isinstance(row, dict) or row.get("form") not in (
                    "10-K",
                    "10-K/A",
                    "10-Q",
                    "10-Q/A",
                ):
                    continue
                try:
                    end = date.fromisoformat(row["end"])
                    available = normalize_legacy_date_only(row["filed"])
                    if available.epoch_ns > cutoff:
                        continue
                    start = date.fromisoformat(row["start"]) if kind == "duration" else None
                except (KeyError, TypeError, ValueError):
                    continue
                if start and not duration_bounds[0] <= (end - start).days + 1 <= duration_bounds[1]:
                    continue
                value = row.get("val")
                if isinstance(value, Decimal):
                    value = format(value, "f")
                number = decimal_value(value)
                if not row.get("accn"):
                    continue
                found.append(
                    (
                        priority,
                        available.epoch_ns,
                        {
                            "entity_id": "sec_cik:" + cik,
                            "cik": cik,
                            "metric": metric,
                            "value": str(number),
                            "unit": unit,
                            "concept": "us-gaap:" + tag,
                            "period_type": period,
                            "period_start": start.isoformat() if start else None,
                            "period_end": end.isoformat(),
                            "available_at": available.iso_utc,
                            "filed_at": row["filed"],
                            "availability_policy": available.normalization_policy,
                            "accession": row["accn"],
                            "form": row["form"],
                            "source_id": "SEC",
                            "validation_state": "SOURCE_UNVALIDATED",
                            "transformation_version": VERSION,
                        },
                    )
                )
        return found

    anchors = set()
    for anchor in ("revenue", "net_income", "operating_income"):
        anchors.update(row["period_end"] for _, _, row in candidates(anchor))
    observations, conflicts, missing = [], [], []
    for metric in metrics:
        groups = {}
        for priority, at, row in candidates(metric):
            if METRICS[metric][1] == "instant" and row["period_end"] not in anchors:
                continue
            groups.setdefault((row["period_start"], row["period_end"]), []).append(
                (priority, at, row)
            )
        chosen = []
        for _, group in sorted(groups.items(), key=lambda item: item[0][1]):
            priority = min(p for p, _, _ in group)
            latest = max(at for p, at, _ in group if p == priority)
            rows = [row for p, at, row in group if p == priority and at == latest]
            if len({decimal_value(row["value"]) for row in rows}) != 1:
                conflicts.append(
                    {
                        "metric": metric,
                        "period_end": rows[0]["period_end"],
                        "reason": "CONFLICTING_SAME_AVAILABILITY",
                        "candidates": rows,
                    }
                )
                continue
            chosen.append(rows[0])
        if not chosen:
            missing.append(metric)
        observations.extend(chosen[-lookback:])
    return {
        "entity": {
            "entity_id": "sec_cik:" + cik,
            "cik": cik,
            "display_name": company.get("entityName"),
        },
        "observations": observations,
        "conflicts": conflicts,
        "missing_metrics": missing,
        "coverage": {
            "financial_validation": "NOT_INDEPENDENTLY_VALIDATED",
            "taxonomy": "us-gaap",
            "currency_conversion": False,
            "complete_financial_statements": False,
        },
    }
