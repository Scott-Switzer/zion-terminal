from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_EPOCH = date(1970, 1, 1)
_INSTANT = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})T(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})(?:\.(?P<fraction>\d{1,9}))?(?P<offset>Z|[+-]\d{2}:\d{2})$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TEMPORAL_SCHEMA_VERSION = "financial-temporal-v1"
TEMPORAL_CONTRACT_SHA256 = "5d225691cb60251d1997bb8d749267da845f0b3cda32137cbf76c3fbd2783b1d"


class TemporalError(ValueError):
    """Raised when a value violates the Financial Temporal Core V1 contract."""


@dataclass(frozen=True)
class TemporalInstant:
    iso_utc: str
    epoch_ns: int
    precision: str
    source_timezone: str | None
    source_value: str | None
    normalization_policy: str

    @property
    def as_datetime(self) -> datetime:
        seconds, nanos = divmod(self.epoch_ns, 1_000_000_000)
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds, microseconds=nanos // 1000)


def _precision(fraction: str | None) -> str:
    if not fraction:
        return "second"
    if len(fraction) <= 3:
        return "millisecond"
    if len(fraction) <= 6:
        return "microsecond"
    return "nanosecond"


def _epoch_ns(utc: datetime, fraction: str | None) -> int:
    seconds = (utc.date() - _EPOCH).days * 86_400 + utc.hour * 3_600 + utc.minute * 60 + utc.second
    nanos = int((fraction or "").ljust(9, "0") or "0")
    return seconds * 1_000_000_000 + nanos


def _iso_from_epoch(epoch_ns: int, *, fractional_digits: int | None = None) -> str:
    seconds, nanos = divmod(epoch_ns, 1_000_000_000)
    instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    if fractional_digits is None:
        fractional_digits = 9
    if fractional_digits <= 0:
        suffix = ""
    else:
        suffix = f".{nanos:09d}"[: fractional_digits + 1]
    return instant.strftime("%Y-%m-%dT%H:%M:%S") + suffix + "Z"


def parse_instant(value: Any, *, field: str = "instant") -> TemporalInstant:
    if not isinstance(value, str) or not value:
        raise TemporalError(f"{field} must be a timezone-aware RFC3339 instant")
    match = _INSTANT.fullmatch(value)
    if not match:
        raise TemporalError(f"{field} must include an explicit Z or numeric UTC offset")
    fraction = match.group("fraction")
    try:
        base = datetime.fromisoformat(f"{match.group('day')}T{match.group('hour')}:{match.group('minute')}:{match.group('second')}")
        offset_text = match.group("offset")
        offset = timezone.utc if offset_text == "Z" else timezone(timedelta(hours=int(offset_text[1:3]), minutes=int(offset_text[4:6])) * (1 if offset_text[0] == "+" else -1))
        utc = base.replace(tzinfo=offset).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise TemporalError(f"{field} is not a valid instant") from exc
    epoch_ns = _epoch_ns(utc, fraction)
    return TemporalInstant(_iso_from_epoch(epoch_ns, fractional_digits=len(fraction) if fraction else 0), epoch_ns, _precision(fraction), offset_text, value, "rfc3339_utc")


def normalize_source_instant(value: Any, *, field: str = "source instant") -> TemporalInstant:
    if isinstance(value, str) and _DATE.fullmatch(value):
        return normalize_date_only(value)
    return parse_instant(value, field=field)


def normalize_legacy_date_only(value: Any, *, policy: str = "DATE_ONLY_NEXT_DAY_ET_V1") -> TemporalInstant:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise TemporalError("legacy date-only value must be YYYY-MM-DD")
    if policy != "DATE_ONLY_NEXT_DAY_ET_V1":
        raise TemporalError(f"unsupported legacy date-only policy: {policy}")
    try:
        source_date = date.fromisoformat(value)
        zone = ZoneInfo("America/New_York")
        local_midnight = datetime.combine(source_date + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise TemporalError("invalid legacy date-only value") from exc
    utc = local_midnight.astimezone(timezone.utc)
    epoch_ns = _epoch_ns(utc, None)
    return TemporalInstant(_iso_from_epoch(epoch_ns, fractional_digits=0), epoch_ns, "date", "America/New_York", value, policy)


def from_epoch_ns(epoch_ns: int, *, precision: str = "nanosecond", source_value: str | None = None) -> TemporalInstant:
    if isinstance(epoch_ns, bool) or not isinstance(epoch_ns, int):
        raise TemporalError("epoch_ns must be a signed integer")
    return TemporalInstant(_iso_from_epoch(epoch_ns, fractional_digits={"date": 0, "second": 0, "millisecond": 3, "microsecond": 6, "nanosecond": 9}.get(precision)), epoch_ns, precision, "UTC", source_value, "epoch_ns")


def normalize_date_only(value: Any, *, policy: str = "date_only_end_of_day_utc") -> TemporalInstant:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise TemporalError("date-only value must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise TemporalError("date-only value is invalid") from exc
    if policy != "date_only_end_of_day_utc":
        raise TemporalError(f"unsupported date-only policy: {policy}")
    epoch_ns = ((parsed - _EPOCH).days * 86_400 + 86_399) * 1_000_000_000 + 999_999_999
    return TemporalInstant(_iso_from_epoch(epoch_ns, fractional_digits=9), epoch_ns, "date", "UTC", value, policy)


def parse_local_civil(value: Any, timezone_name: str) -> TemporalInstant:
    if not isinstance(value, str) or "T" not in value or re.search(r"(Z|[+-]\d{2}:\d{2})$", value):
        raise TemporalError("local civil time must be naive and require a named timezone")
    try:
        local = datetime.fromisoformat(value)
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise TemporalError("invalid local civil time or timezone") from exc
    if local.tzinfo is not None:
        raise TemporalError("local civil time must not contain an offset")
    candidates = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(zone).replace(tzinfo=None) == local:
            candidates.append(aware)
    if not candidates:
        raise TemporalError("nonexistent local civil time")
    if len(candidates) == 2 and candidates[0].utcoffset() != candidates[1].utcoffset():
        raise TemporalError("ambiguous local civil time requires an explicit offset or fold")
    fraction = local.strftime("%f").rstrip("0") or None
    utc = candidates[0].astimezone(timezone.utc)
    epoch_ns = _epoch_ns(utc, fraction)
    return TemporalInstant(_iso_from_epoch(epoch_ns, fractional_digits=len(fraction) if fraction else 0), epoch_ns, _precision(fraction), timezone_name, value, "named_timezone")


@dataclass(frozen=True)
class MarketSession:
    calendar_id: str
    session_date: str
    exchange_timezone: str
    regular_open: str | None
    regular_close: str | None
    is_holiday: bool
    is_early_close: bool
    session_phase: str = "CLOSED"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return next_month - timedelta(days=(next_month.weekday() - weekday) % 7 + 1)


def us_equity_session(value: str | date) -> MarketSession:
    day = date.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(day, date):
        raise TemporalError("session_date must be a calendar date")
    holidays = {
        date(day.year, 1, 1), _nth_weekday(day.year, 1, 0, 3), _nth_weekday(day.year, 2, 0, 3),
        _last_weekday(day.year, 5, 0), _nth_weekday(day.year, 9, 0, 1), _nth_weekday(day.year, 11, 3, 4), date(day.year, 12, 25)
    }
    if day.year >= 2022: holidays.add(date(day.year, 6, 19))
    observed = {d + timedelta(days=1) if d.weekday() == 5 else d - timedelta(days=1) if d.weekday() == 6 else d for d in holidays}
    if day.weekday() >= 5 or day in observed:
        return MarketSession("XNYS", day.isoformat(), "America/New_York", None, None, True, False)
    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    early = day == thanksgiving + timedelta(days=1) or (day.month == 7 and day.day == 3)
    return MarketSession("XNYS", day.isoformat(), "America/New_York", "09:30:00", "13:00:00" if early else "16:00:00", False, early, "REGULAR")


def validate_leakage(rows: list[dict[str, Any]], as_of: str) -> None:
    cutoff = parse_instant(as_of, field="as_of").epoch_ns
    for row in rows:
        value = row.get("available_at")
        if not value:
            raise TemporalError("available_at is required for leakage validation")
        available = parse_instant(value, field="available_at").epoch_ns
        if available > cutoff:
            raise TemporalError("future observation leaked past as_of")
        input_times = row.get("input_available_at", [])
        for input_time in input_times:
            if parse_instant(input_time, field="input_available_at").epoch_ns > available:
                raise TemporalError("derived observation precedes an input availability instant")
