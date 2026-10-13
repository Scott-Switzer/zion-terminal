"""Source-preserving observations and explicitly comparable revenue growth."""

import re
from datetime import date
from decimal import Decimal, localcontext


class PrivateFinancialsError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(f"{code}: {message}")


def fail(code: str, message: str):
    raise PrivateFinancialsError(code, message)


def decimal_value(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        fail("INVALID_OBSERVATION", "exact decimal text is required")
    text = str(value)
    if not re.fullmatch(r"-?\d{1,80}(?:\.\d{1,40})?", text):
        fail("INVALID_OBSERVATION", "bounded finite decimal text is required")
    return Decimal(text)


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def normalize_observation(row: dict, *, release_id: str) -> dict:
    value = row.get("value_decimal", row.get("value"))
    if value is not None:
        decimal_value(value)
        value = str(value)
    basis = row.get("accounting_basis")
    basis_source = "EXPLICIT_SOURCE_BASIS" if basis else None
    # FUNDAMENTAL_METRICS.md defines published revenue as consolidated company
    # revenue. This identifies the canonical metric, not an inferred XBRL tag or
    # accounting framework. Require the published SEC producer lineage.
    if (
        not basis
        and row.get("metric_id") == "revenue"
        and row.get("source_id") == "SEC"
        and row.get("derivation_type") == "reported"
        and row.get("producer_release")
    ):
        basis = "zion:consolidated-reported-revenue:v1"
        basis_source = "CANONICAL_METRIC_DEFINITION"
    return {
        "entity_id": row.get("entity_id"),
        "instrument_id": row.get("instrument_id"),
        "metric": row.get("metric_id", row.get("metric")),
        "value": value,
        "unit": row.get("unit"),
        "accounting_basis": row.get("accounting_basis"),
        "comparison_basis": basis,
        "comparison_basis_source": basis_source,
        "source_id": row.get("source_id"),
        "derivation_type": row.get("derivation_type"),
        "period_type": row.get("period_type"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "available_at": row.get("available_at"),
        "retrieved_at": row.get("ingested_at"),
        "source_record_id": row.get("source_record_id"),
        "source_revision_id": row.get("source_revision_id"),
        "evidence_id": row.get("evidence_id"),
        "producer_release": row.get("producer_release"),
        "serving_release_id": release_id,
        "validation_state": "SOURCE_UNVALIDATED",
        "transformation_version": "zion-private-financial-observation-v1",
    }


def annual_revenue_growth(observations: list[dict], *, years: int = 5) -> dict:
    if type(years) is not int or not 1 <= years <= 10:
        fail("INVALID_REQUEST", "years must be an integer from 1 to 10")
    rows = sorted(observations, key=lambda r: r.get("period_end") or "")
    if len({r.get("period_end") for r in rows}) != len(rows):
        fail("AMBIGUOUS_PERIOD", "duplicate fiscal periods require resolution")
    if len(rows) < years + 1:
        fail("INCOMPLETE_HISTORY", "growth requires consecutive annual observations")
    rows = rows[-(years + 1) :]
    if any(not r.get("comparison_basis") for r in rows):
        fail("COMPARABILITY_NOT_ESTABLISHED", "accounting basis is not established")
    keys = ("entity_id", "metric", "unit", "comparison_basis", "serving_release_id")
    if any(not rows[0].get(k) for k in keys):
        fail("COMPARABILITY_NOT_ESTABLISHED", "required comparability metadata is missing")
    if (
        rows[0]["metric"] != "revenue"
        or any(r.get("period_type") != "annual" for r in rows)
        or any(r.get(k) != rows[0].get(k) for r in rows for k in keys)
    ):
        fail("INCOMPARABLE_OBSERVATIONS", "issuer, metric, units, basis or release differs")
    try:
        bounds = [
            (date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"]))
            for r in rows
        ]
    except (KeyError, TypeError, ValueError):
        fail("COMPARABILITY_NOT_ESTABLISHED", "fiscal period bounds are missing or invalid")
    if any(not 330 <= (end - start).days + 1 <= 380 for start, end in bounds):
        fail("INCOMPARABLE_OBSERVATIONS", "observations must cover full fiscal years")
    if any((bounds[i][0] - bounds[i - 1][1]).days != 1 for i in range(1, len(bounds))):
        fail("INCOMPLETE_HISTORY", "fiscal periods are not consecutive")
    values = [decimal_value(r.get("value")) for r in rows]
    if any(v <= 0 for v in values):
        fail("NON_POSITIVE_REVENUE", "growth requires positive comparable revenue")
    with localcontext() as ctx:
        ctx.prec = 128
        growth = [
            {
                "period_end": rows[i]["period_end"],
                "prior_period_end": rows[i - 1]["period_end"],
                "percent": decimal_text((values[i] / values[i - 1] - 1) * 100),
            }
            for i in range(1, len(rows))
        ]
        cumulative = decimal_text((values[-1] / values[0] - 1) * 100)
    return {
        "years": years,
        "year_over_year": growth,
        "cumulative_percent": cumulative,
        "formula": "(current / prior - 1) * 100",
        "inputs": rows,
    }
