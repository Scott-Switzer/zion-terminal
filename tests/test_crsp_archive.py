"""Synthetic-only CRSP identity, locator, and native price semantics tests."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/cloudflare-query/src"))
import crsp_archive as c


def test_reused_ticker_requires_stable_identity_or_date():
    rows = [
        dict(permno=1, ticker="OLD", comnam="First", namedt="1990-01-01", nameenddt="2000-12-31"),
        dict(permno=2, ticker="OLD", comnam="Second", namedt="2010-01-01", nameenddt="2020-12-31"),
    ]
    catalog = c.catalog(rows)
    assert len(c.search(catalog, "OLD")) == 2
    assert c.resolve(catalog, symbol="OLD", as_of="1995-01-01")["permno"] == 1
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        c.resolve(catalog, symbol="OLD")
    assert c.resolve(catalog, permno=2)["permno"] == 2


def test_rowgroup_plan_keeps_native_identity_and_time_bounds():
    groups = [
        dict(
            source="a",
            row_group=0,
            permnos={1: 2, 2: 1},
            date_min="1990-01-01",
            date_max="1992-12-31",
        ),
        dict(source="b", row_group=0, permnos={1: 3}, date_min="2010-01-01", date_max="2011-12-31"),
    ]
    loc = c.locators(groups)
    assert c.plan_history(loc["1"], start="1991-01-01", end="1992-01-01") == loc["1"][:1]
    assert loc["1"][0]["rows"] == 2
    assert len(loc["2"]) == 1


def test_negative_price_and_missing_return_are_preserved():
    p = c.native_point(
        dict(permno=1, dlycaldt="1990-01-02", dlyprc=-4.5, dlyret=None, dlyretx=0.01, dlyvol=0)
    )
    assert p["native_price"] == "-4.5"
    assert p["price_magnitude"] == "4.5"
    assert p["price_observation_type"] == "NEGATIVE_NATIVE_PRICE_BID_ASK_INDICATOR"
    assert p["total_return"] is None
    assert p["adjusted_close"] is None
    assert p["volume"] == "0"


def test_release_hashes_shards_and_does_not_copy_prices():
    cat = c.catalog(
        [
            dict(
                permno=1,
                ticker="SYN",
                comnam="Synthetic",
                namedt="1990-01-01",
                nameenddt="2000-01-01",
            )
        ]
    )
    objects, pointer = c.build(
        cat,
        {
            "1": [
                dict(source="a", row_group=0, rows=2, date_min="1990-01-01", date_max="2000-01-01")
            ]
        },
        {"a": dict(key="raw/wrds/synthetic.parquet", sha256="a" * 64, bytes=10)},
        coverage_sha256="b" * 64,
    )
    import hashlib
    import json

    root = json.loads(objects[pointer["key"]])
    assert root["security_count"] == 1
    assert root["price_rows_copied"] == 0
    for ref in root["shards"].values():
        assert hashlib.sha256(objects[ref["key"]]).hexdigest() == ref["sha256"]


def test_disjoint_permno_runs_become_bounded_absolute_source_spans():
    spans = c.row_spans(
        [1, 1, 2, 1, 3],
        offset=100,
        row_group=2,
        source="a",
        date_min="1990-01-01",
        date_max="1991-01-01",
    )
    assert [(s["permno"], s["offset"], s["rows"]) for s in spans] == [
        (1, 100, 2),
        (2, 102, 1),
        (1, 103, 1),
        (3, 104, 1),
    ]
    plans = c.plan_history([s for s in spans if s["permno"] == 1], max_rows=1)
    assert [(s["offset"], s["rows"]) for s in plans] == [(100, 1), (101, 1), (103, 1)]


def test_backend_dates_and_nonfinite_tokens_are_explicit_gaps():
    p = c.native_point(
        dict(
            permno=1,
            dlycaldt="1990-01-02T00:00:00.000Z",
            dlyprc={"non_finite": "NaN"},
            dlyret=float("nan"),
        )
    )
    assert p["session_date"] == "1990-01-02"
    assert p["native_price"] is None
    assert p["missing_reasons"]["native_price"] == "NON_FINITE_NATIVE_VALUE"
    assert p["missing_reasons"]["total_return"] == "NON_FINITE_NATIVE_VALUE"


def test_year_spans_bound_dates_and_verify_order():
    spans = c.row_spans(
        [1, 1, 1, 1],
        dates=["1990-01-02", "1990-12-31", "1991-01-02", "1991-01-01"],
        offset=0,
        row_group=0,
        source="a",
        date_min=None,
        date_max=None,
    )
    assert len(spans) == 2
    assert spans[0]["date_min"] == "1990-01-02"
    assert spans[0]["date_max"] == "1990-12-31"
    assert spans[0]["date_monotonic"] is True
    assert spans[1]["date_monotonic"] is False
    assert spans[1]["date_min"] == "1991-01-01"


def test_builder_projects_existing_parquet_and_publishes_verified_year_index(tmp_path):
    import hashlib
    import importlib.util
    import io
    import json
    from datetime import date

    import pyarrow as pa
    import pyarrow.parquet as pq

    spec = importlib.util.spec_from_file_location(
        "build_crsp_stock_index",
        Path(__file__).resolve().parents[1] / "scripts/build_crsp_stock_index.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def parquet(data):
        stream = io.BytesIO()
        pq.write_table(pa.table(data), stream)
        return stream.getvalue()

    raw_daily = parquet(
        dict(
            permno=[1, 1, 1, 2],
            dlycaldt=[date(1990, 1, 1), date(1990, 1, 2), date(1991, 1, 1), date(1991, 1, 2)],
            dlyprc=[-1.0, 2.0, 3.0, 4.0],
        )
    )
    raw_names = parquet(
        dict(
            permno=[1, 2],
            ticker=["SYN", "OLD"],
            comnam=["Synthetic", "Other"],
            namedt=[date(1990, 1, 1)] * 2,
            nameenddt=[date(2000, 1, 1)] * 2,
            permco=[5, 6],
        )
    )

    class Store:
        def __init__(self):
            self.objects = {"raw/wrds/daily": raw_daily, "raw/wrds/names": raw_names}
            self.calls = []

        def head_object(self, **kw):
            return dict(ContentLength=len(self.objects[kw["Key"]]), ETag='"synthetic"')

        def get_object(self, **kw):
            self.calls.append(kw)
            raw = self.objects[kw["Key"]]
            if "Range" in kw:
                begin, end = map(int, kw["Range"].removeprefix("bytes=").split("-"))
                raw = raw[begin : end + 1]
            return dict(Body=io.BytesIO(raw), ContentLength=len(raw))

        def put_object(self, **kw):
            self.objects[kw["Key"]] = kw["Body"]

    store = Store()

    def part(key, raw, rows):
        return dict(
            key=key, source_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), rows=rows
        )

    coverage = dict(
        datasets=[
            dict(
                dataset="crsp_a_stock__stkdlysecurityprimarydata",
                release="synthetic",
                rows=4,
                objects=[part("raw/wrds/daily", raw_daily, 4)],
            ),
            dict(
                dataset="crsp_a_stock__stocknames", objects=[part("raw/wrds/names", raw_names, 2)]
            ),
        ]
    )
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(coverage))
    pointer = mod.index(path, tmp_path / "index", store, publish=True)
    manifest = json.loads(store.objects[pointer["key"]])
    assert manifest["indexed_rows"] == 4
    assert manifest["catalog"][0]["aliases"] == ["SYN"]
    assert "intervals" not in manifest["catalog"][0]
    suffix = c.digest(b"1")[:2]
    entry = json.loads(store.objects[manifest["shards"][suffix]["key"]])["securities"]["1"]
    assert [(x["calendar_year"], x["rows"]) for x in entry["fragments"]] == [
        ("1990", 2),
        ("1991", 1),
    ]
    assert all(x.get("IfMatch") == '"synthetic"' for x in store.calls if "Range" in x)
