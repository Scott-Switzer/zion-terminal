"""Treasury histories retain source gaps and reject ambiguous or corrupt CSV."""

from datetime import datetime, timezone

import pytest

from zion_terminal.history_sources import treasury


def task(year=2025, dataset="treasury_nominal"):
    return next(t for t in treasury.tasks(year, year) if t["dataset"] == dataset)


def test_yearly_tasks_respect_source_inceptions_and_current_utc_year():
    result = treasury.tasks(1990, 2004)
    assert len(result) == 17
    assert result[0]["id"] == "treasury_nominal:1990"
    assert {t["id"] for t in result if t["dataset"] == "treasury_real"} == {
        "treasury_real:2003",
        "treasury_real:2004",
    }
    assert all(t["url"].startswith("https://home.treasury.gov/") for t in result)
    assert len(treasury.tasks()) == 2 * datetime.now(timezone.utc).year - 3991


@pytest.mark.parametrize("start,end", [(1989, 1991), (2004, 2003), (True, 2025), (2025, 10000)])
def test_invalid_task_ranges_fail(start, end):
    with pytest.raises(ValueError):
        treasury.tasks(start, end)


def test_nominal_csv_retains_precision_missing_cells_and_new_short_maturity():
    rows = treasury.parse(
        b"Date,1 Mo,1.5 Month,10 Yr,30 Yr\r\n01/02/2025,4.450,N/A,-0.12,\r\n", task()
    )
    assert rows == [
        {
            "series_id": "us_treasury:nominal:1m",
            "observation_date": "2025-01-02",
            "value_decimal": "4.450",
            "missing_reason": None,
            "unit": "percent",
            "asset_class": "rates",
            "label": "US Treasury nominal par yield 1 Mo",
        },
        {
            "series_id": "us_treasury:nominal:1.5m",
            "observation_date": "2025-01-02",
            "value_decimal": None,
            "missing_reason": "SOURCE_NA",
            "unit": "percent",
            "asset_class": "rates",
            "label": "US Treasury nominal par yield 1.5 Month",
        },
        {
            "series_id": "us_treasury:nominal:10y",
            "observation_date": "2025-01-02",
            "value_decimal": "-0.12",
            "missing_reason": None,
            "unit": "percent",
            "asset_class": "rates",
            "label": "US Treasury nominal par yield 10 Yr",
        },
        {
            "series_id": "us_treasury:nominal:30y",
            "observation_date": "2025-01-02",
            "value_decimal": None,
            "missing_reason": "SOURCE_EMPTY",
            "unit": "percent",
            "asset_class": "rates",
            "label": "US Treasury nominal par yield 30 Yr",
        },
    ]


def test_real_negative_yield_and_zero_remain_distinct_from_missing():
    rows = treasury.parse(
        b"\xef\xbb\xbfDate,5 YR,10 YR\n01/03/2025,-0.70,0.00\n", task(dataset="treasury_real")
    )
    assert [(r["series_id"], r["value_decimal"], r["missing_reason"]) for r in rows] == [
        ("us_treasury:real:5y", "-0.70", None),
        ("us_treasury:real:10y", "0.00", None),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b"<!doctype html><html>blocked</html>",
        b"Date,Mystery\n01/02/2025,4\n",
        b"Unknown Date,10 Yr\n01/02/2025,4\n",
        b"Date,10 Yr,10 Yr\n01/02/2025,4,4\n",
        b"Date,10 Yr\n12/31/2024,4\n",
        b"Date,10 Yr\n02/30/2025,4\n",
        b"Date,10 Yr\n2025-01-02,4\n",
        b"Date,10 Yr\n01/02/2025,NaN\n",
        b"Date,10 Yr\n01/02/2025,Infinity\n",
        b"Date,10 Yr\n01/02/2025,1_000\n",
        b"Date,10 Yr\n01/02/2025,4,5\n",
        b"Date,10 Yr\n01/02/2025\n",
        b'Date,10 Yr\n01/02/2025,"4\n',
        b"Date,10 Yr\n",
        b"Date\n01/02/2025\n",
        b"\xffDate,10 Yr\n01/02/2025,4\n",
    ],
)
def test_malformed_or_unrecognized_source_refused(raw):
    with pytest.raises(ValueError):
        treasury.parse(raw, task())


def test_conflicting_duplicate_observation_refused_and_identical_deduplicated():
    with pytest.raises(ValueError, match="conflict"):
        treasury.parse(b"Date,10 Yr\n01/02/2025,4.00\n01/02/2025,4.01\n", task())
    rows = treasury.parse(b"Date,10 Yr\n01/02/2025,4.00\n01/02/2025,4.00\n", task())
    assert len(rows) == 1


def test_real_disallows_nominal_maturities_and_task_identity_must_match():
    with pytest.raises(ValueError):
        treasury.parse(b"Date,1 Mo\n01/02/2025,4\n", task(dataset="treasury_real"))
    invalid = dict(task(), id="treasury_real:2025")
    with pytest.raises(ValueError):
        treasury.parse(b"Date,10 Yr\n01/02/2025,4\n", invalid)
