"""Official US Treasury historical daily par yields, expressed in percent.

Source: https://home.treasury.gov/resource-center/data-chart-center/interest-rates/
These observations are yields, not bond prices or forecasts. The observation date
is not a historical publication/availability timestamp and makes no PIT claim.
"""

import csv
import io
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

_BASE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"
_DATASETS = {
    "treasury_nominal": ("daily_treasury_yield_curve", 1990),
    "treasury_real": ("daily_treasury_real_yield_curve", 2003),
}
_NOMINAL = {
    "1 Mo": "1m",
    "1.5 Month": "1.5m",
    "1.5 Mo": "1.5m",
    "2 Mo": "2m",
    "3 Mo": "3m",
    "4 Mo": "4m",
    "6 Mo": "6m",
    "1 Yr": "1y",
    "2 Yr": "2y",
    "3 Yr": "3y",
    "5 Yr": "5y",
    "7 Yr": "7y",
    "10 Yr": "10y",
    "20 Yr": "20y",
    "30 Yr": "30y",
}
_REAL = {f"{years} YR": f"{years}y" for years in (5, 7, 10, 20, 30)}
_REAL.update({f"{years} Yr": f"{years}y" for years in (5, 7, 10, 20, 30)})
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_DATE = re.compile(r"[0-9]{2}/[0-9]{2}/[0-9]{4}\Z")


def _url(dataset: str, year: int) -> str:
    data_type = _DATASETS[dataset][0]
    return (
        f"{_BASE}/daily-treasury-rates.csv/{year}/all?_format=csv"
        f"&field_tdr_date_value={year}&page=&type={data_type}"
    )


def tasks(start_year: int = 1990, end_year: int | None = None) -> list[dict]:
    """Plan one official CSV per available nominal/real calendar year."""
    current_year = datetime.now(timezone.utc).year
    if end_year is None:
        end_year = current_year
    if (
        type(start_year) is not int
        or type(end_year) is not int
        or not 1990 <= start_year <= end_year <= current_year
    ):
        raise ValueError("Treasury years must be integers from 1990 through the current UTC year")
    return [
        {"id": f"{dataset}:{year}", "url": _url(dataset, year), "dataset": dataset}
        for dataset, (_, inception) in _DATASETS.items()
        for year in range(max(start_year, inception), end_year + 1)
    ]


def parse(raw: bytes, task: dict) -> list[dict]:
    """Validate a yearly CSV and retain each published cell, including source gaps.

    Unknown columns and invalid rows fail the entire task. No absent maturity is
    invented. Identical duplicate observations collapse; conflicting ones fail.
    """
    dataset = task.get("dataset")
    if dataset not in _DATASETS:
        raise ValueError("Unknown Treasury dataset")
    match = re.fullmatch(re.escape(dataset) + r":([0-9]{4})", str(task.get("id", "")))
    if not match:
        raise ValueError("Invalid Treasury task identity")
    year = int(match[1])
    if not _DATASETS[dataset][1] <= year <= datetime.now(timezone.utc).year:
        raise ValueError("Treasury task year outside source coverage")
    if task.get("url") != _url(dataset, year):
        raise ValueError("Treasury task URL does not match dataset and year")
    try:
        decoded = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        header = next(reader)
        mapping = _NOMINAL if dataset == "treasury_nominal" else _REAL
        if (
            len(header) < 2
            or header[0] != "Date"
            or len(set(header)) != len(header)
            or any(column not in mapping for column in header[1:])
        ):
            raise ValueError("Unknown or duplicate Treasury CSV columns")
        maturities = [mapping[column] for column in header[1:]]
        if len(set(maturities)) != len(maturities):
            raise ValueError("Duplicate Treasury maturity columns")
        kind = dataset.removeprefix("treasury_")
        observations = {}
        for cells in reader:
            if len(cells) != len(header):
                raise ValueError("Malformed Treasury CSV row width")
            if not _DATE.fullmatch(cells[0]):
                raise ValueError("Unknown Treasury date format")
            observed = datetime.strptime(cells[0], "%m/%d/%Y").date()
            if observed.year != year:
                raise ValueError("Treasury observation date outside task year")
            for column, maturity, cell in zip(header[1:], maturities, cells[1:]):
                cell = cell.strip()
                missing = "SOURCE_EMPTY" if not cell else "SOURCE_NA" if cell == "N/A" else None
                value = None
                if missing is None:
                    if not _NUMBER.fullmatch(cell):
                        raise ValueError("Invalid Treasury decimal value")
                    number = Decimal(cell)
                    if not number.is_finite():
                        raise ValueError("Nonfinite Treasury decimal value")
                    value = format(number, "f")
                record = {
                    "series_id": f"us_treasury:{kind}:{maturity}",
                    "observation_date": observed.isoformat(),
                    "value_decimal": value,
                    "missing_reason": missing,
                    "unit": "percent",
                    "asset_class": "rates",
                    "label": f"US Treasury {kind} par yield {column}",
                }
                key = (record["series_id"], record["observation_date"])
                prior = observations.get(key)
                if prior is not None and prior != record:
                    raise ValueError("Treasury duplicate observation conflict")
                observations[key] = record
        if not observations:
            raise ValueError("Treasury CSV contains no observations")
        return list(observations.values())
    except (UnicodeError, csv.Error, StopIteration, InvalidOperation) as exc:
        raise ValueError("Malformed Treasury CSV") from exc
