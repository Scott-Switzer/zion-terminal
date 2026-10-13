"""Fail-closed financial identity/observation validation; never infers records or links."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal


class FoundationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def instant_ns(value: str) -> int:
    pattern = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})"
    if not isinstance(value, str) or not (match := re.fullmatch(pattern, value)):
        raise FoundationError("INVALID_AVAILABILITY_INSTANT")
    try:
        zone = match[3]
        offset = timezone.utc
        if zone != "Z":
            hours, minutes = map(int, zone[1:].split(":"))
            if hours > 23 or minutes > 59:
                raise ValueError()
            offset = timezone(
                timedelta(minutes=(hours * 60 + minutes) * (-1 if zone[0] == "-" else 1))
            )
        dt = datetime.fromisoformat(match[1]).replace(tzinfo=offset)
        delta = dt.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + int(
            (match[2] or "").ljust(9, "0")
        )
    except (ValueError, OverflowError) as exc:
        raise FoundationError("INVALID_AVAILABILITY_INSTANT") from exc


def resolve_identifier(
    links: list[dict], namespace: str, value: str, *, valid_at: str, as_of: str
) -> dict:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise FoundationError("INVALID_IDENTIFIER")
    if namespace not in {"SEC_CIK", "CRSP_PERMNO", "COMPUSTAT_GVKEY", "ISIN", "CUSIP", "TICKER"}:
        raise FoundationError("UNSUPPORTED_IDENTIFIER_NAMESPACE")
    if namespace == "SEC_CIK" and not re.fullmatch(r"\d{10}", value):
        raise FoundationError("INVALID_IDENTIFIER")
    when, knowledge = instant_ns(valid_at), instant_ns(as_of)
    eligible = []
    for link in links:
        if link.get("namespace") != namespace or link.get("identifier") != value:
            continue
        if link.get("known_at_source") not in {"SOURCE_PUBLICATION", "SOURCE_REPORTED_ACCEPTANCE"}:
            raise FoundationError("INVALID_LINK_AVAILABILITY_EVIDENCE")
        if not link.get("valid_from") or link.get("valid_to_kind") == "UNKNOWN":
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", link.get("source_sha256", "")):
            raise FoundationError("LINK_PROVENANCE_REQUIRED")
        start = instant_ns(link["valid_from"])
        end = instant_ns(link["valid_to"]) if link.get("valid_to") else None
        if end is None and link.get("valid_to_kind") != "SOURCE_OPEN_END":
            continue
        if end is not None and end <= start:
            raise FoundationError("INVALID_LINK_VALIDITY_INTERVAL")
        if (
            start <= when
            and (end is None or when < end)
            and instant_ns(link["known_at"]) <= knowledge
        ):
            eligible.append(link)
    if not eligible:
        raise FoundationError("HISTORICAL_IDENTITY_NOT_ESTABLISHED")
    if len({r["entity_id"] for r in eligible}) != 1:
        raise FoundationError("AMBIGUOUS_HISTORICAL_IDENTITY")
    return {
        "entity_id": eligible[0]["entity_id"],
        "source_evidence": [r["source_sha256"] for r in eligible],
    }


def validate_observation(row: dict, *, as_of: str) -> dict:
    """Partial semantic checks; enforce JSON Schema and entitlements before serving."""
    required = {
        "source_observation_id",
        "source_sha256",
        "source_concept",
        "native_unit",
        "unit",
        "period_end",
        "available_at",
        "availability_policy",
        "layer",
        "reporting_status",
        "value_decimal",
    }
    if required - row.keys():
        raise FoundationError("SOURCE_CONTEXT_INCOMPLETE")
    if row["layer"] not in {"RAW", "NORMALIZED_RESEARCH"}:
        raise FoundationError("DERIVED_REQUIRES_SEPARATE_FORMULA_CONTRACT")
    if row["availability_policy"] not in {"EXACT_SOURCE_ACCEPTANCE", "DATE_ONLY_NEXT_DAY_ET_V1"}:
        raise FoundationError("AVAILABILITY_EVIDENCE_REQUIRED")
    if instant_ns(row["available_at"]) > instant_ns(as_of):
        raise FoundationError("OBSERVATION_NOT_AVAILABLE_AT_DATE")
    if row["unit"] != row["native_unit"]:
        raise FoundationError("UNIT_CONVERSION_NOT_AUTHORIZED")
    if not re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"]):
        raise FoundationError("SOURCE_VERSION_REQUIRED")
    try:
        end = date.fromisoformat(row["period_end"])
        start = date.fromisoformat(row["period_start"]) if row.get("period_start") else None
    except (ValueError, TypeError) as exc:
        raise FoundationError("INVALID_FINANCIAL_PERIOD") from exc
    if start and start > end:
        raise FoundationError("INVALID_FINANCIAL_PERIOD")
    for name in ("source_observation_id", "source_concept", "unit", "native_unit"):
        if not isinstance(row[name], str) or not row[name]:
            raise FoundationError("SOURCE_CONTEXT_INCOMPLETE")
    value = row["value_decimal"]
    if value is None:
        if row["reporting_status"] != "SOURCE_REPORTED_NULL":
            raise FoundationError("ABSENT_OBSERVATION_IS_NOT_A_NULL_FINANCIAL_RECORD")
    elif (
        not isinstance(value, str)
        or not re.fullmatch(r"-?\d+(?:\.\d+)?", value)
        or not Decimal(value).is_finite()
    ):
        raise FoundationError("EXACT_FINITE_DECIMAL_REQUIRED")
    elif row["reporting_status"] != "SOURCE_REPORTED_VALUE":
        raise FoundationError("REPORTING_STATUS_MISMATCH")
    if row.get("period_start") and row["period_start"] > row["period_end"]:
        raise FoundationError("INVALID_FINANCIAL_PERIOD")
    return {
        **row,
        "canonical_eligible": False,
        "quality": "SOURCE_UNVALIDATED",
        "promotion_gate": "SEPARATE_APPROVAL_REQUIRED",
    }
