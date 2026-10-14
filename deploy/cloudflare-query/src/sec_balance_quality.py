"""Versioned comparability guard around the immutable SEC balance comparator.

No earlier-vintage substitution: retain the frozen core's selected latest facts,
but do not present their difference as an accounting identity when filing
vintages, periods, or monetary units differ.
"""

from sec_engine import HistoryError
from sec_engine import balance_checks as legacy_balance_checks
from temporal_core import parse_instant

VERSION = "sec-balance-same-vintage-v1"
IDENTITY = "Assets = LiabilitiesAndStockholdersEquity"


def _unit_failure_context(data, cutoff, end):
    """Recover the core's chosen source IDs when its arithmetic rejects units.

    This mirrors frozen latest/tie/rejection selection only for the exception
    path; it never changes amounts or replaces the latest filing with an older
    compatible one.
    """
    selected = []
    for concept in ("us-gaap:Assets", "us-gaap:LiabilitiesAndStockholdersEquity"):
        rows = [
            row
            for row in data["observations"]
            if row["concept"] == concept
            and row["metric"].startswith("balance_component:")
            and row["period_start"] is None
            and row["period_end"] == end
            and row["available_at"] <= cutoff
        ]
        if not rows:
            continue
        latest = max(row["available_at"] for row in rows)
        versions = [row for row in rows if row["available_at"] == latest]
        invalid = any(
            row["metric"] == versions[0]["metric"]
            and row["period_end"] == end
            and row["period_start"] is None
            and latest <= row["available_at"] <= cutoff
            for row in data.get("rejected_contexts", [])
        )
        values = {row["value_decimal"] for row in versions}
        if not invalid and len(values) == 1 and None not in values:
            selected.append(min(versions, key=lambda row: row["source_observation_id"]))
    return {
        "period_end": end,
        "status": "UNAVAILABLE",
        "identity": IDENTITY,
        "reason": "LATEST_BALANCE_TOTALS_DIFFERENT_UNIT_OR_CURRENCY",
        "source_observation_ids": sorted(row["source_observation_id"] for row in selected),
        "available_at": max((row["available_at"] for row in selected), default=None),
        "qualification": "LATEST_REPORTED_TOTALS_NOT_COMPARABLE;NO_OLDER_VINTAGE_SUBSTITUTION",
    }


def _legacy_checks(data, as_of, date_from, date_to):
    try:
        return legacy_balance_checks(data, as_of, date_from, date_to)
    except HistoryError as error:
        if error.code != "INCOMPATIBLE_COMPONENT_UNITS":
            raise
    # One incompatible period must not suppress valid checks for other periods.
    cutoff = parse_instant(as_of).iso_utc
    ends = sorted(
        {
            row["period_end"]
            for row in data["observations"]
            if row["metric"].startswith("balance_component:")
            and not row["period_start"]
            and date_from <= row["period_end"] <= date_to
            and row["available_at"] <= cutoff
        }
    )
    checks = []
    for end in ends:
        try:
            checks.extend(legacy_balance_checks(data, as_of, end, end))
        except HistoryError as error:
            if error.code != "INCOMPATIBLE_COMPONENT_UNITS":
                raise
            checks.append(_unit_failure_context(data, cutoff, end))
    return checks


def balance_checks(data, as_of, date_from, date_to):
    """Same signature as sec_engine.balance_checks; opt in by manifest version."""
    source = {row["source_observation_id"]: row for row in data["observations"]}
    output = []
    for original in _legacy_checks(data, as_of, date_from, date_to):
        check = {**original, "balance_quality_version": VERSION}
        if check["status"] not in ("PASS", "FAIL"):
            output.append(check)
            continue
        ids = check["source_observation_ids"]
        selected = [source[sid] for sid in ids if sid in source]
        reason = None
        if len(selected) != 2 or any(
            not row.get(key) for row in selected for key in ("accession", "form", "available_at")
        ):
            reason = "LATEST_BALANCE_TOTAL_CONTEXT_MISSING"
        elif any(not row.get("unit") or not row.get("currency") for row in selected) or any(
            selected[0].get(key) != selected[1].get(key) for key in ("unit", "currency")
        ):
            reason = "LATEST_BALANCE_TOTALS_DIFFERENT_UNIT_OR_CURRENCY"
        elif any(
            row.get("period_start") is not None or row.get("period_end") != check["period_end"]
            for row in selected
        ):
            reason = "LATEST_BALANCE_TOTALS_DIFFERENT_PERIOD_SCOPE"
        elif any(
            selected[0].get(key) != selected[1].get(key)
            for key in ("accession", "form", "available_at")
        ):
            reason = "LATEST_BALANCE_TOTALS_DIFFERENT_FILING_VINTAGES"
        if reason:
            check.update(
                status="UNAVAILABLE",
                reason=reason,
                qualification="LATEST_REPORTED_TOTALS_NOT_COMPARABLE;NO_OLDER_VINTAGE_SUBSTITUTION",
            )
            check.pop("difference_decimal", None)
            check["available_at"] = max(
                (row["available_at"] for row in selected), default=check.get("available_at")
            )
        output.append(check)
    return output
