"""CRSP is the default historical stock source; tests contain synthetic records only."""

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
a = importlib.import_module("crsp_archive")
api = importlib.import_module("crsp_stock_history_api")
router = importlib.import_module("stock_history_api")


class Object:
    def __init__(self, raw):
        self.raw, self.size = raw, len(raw)

    async def text(self):
        return self.raw.decode()


class Bucket:
    def __init__(self, objects):
        self.objects, self.reads = objects, []

    async def get(self, key):
        self.reads.append(key)
        return Object(self.objects[key]) if key in self.objects else None


def setup(monkeypatch):
    entries = a.catalog(
        [
            dict(
                permno=10001,
                ticker="OLD",
                comnam="Synthetic issuer",
                namedt="2000-01-01",
                nameenddt="2000-12-31",
            ),
            dict(
                permno=10001,
                ticker="NEW",
                comnam="Synthetic issuer",
                namedt="2001-01-01",
                nameenddt="2001-12-31",
            ),
            dict(
                permno=10002,
                ticker="OLD",
                comnam="Different issuer",
                namedt="2002-01-01",
                nameenddt="2002-12-31",
            ),
        ]
    )
    for e in entries.values():
        e.update(date_min="2000-01-03", date_max="2001-01-02", normalized_rows=3)
    source = dict(
        key="raw/wrds/crsp__stkdlysecurityprimarydata/releases/test/part-000000.parquet",
        sha256="a" * 64,
        etag="source-version",
        bytes=100,
        rows=3,
        date_min="2000-01-03",
        date_max="2001-01-02",
    )
    locations = {
        "10001": [
            dict(
                source="part",
                row_group=0,
                offset=0,
                rows=2,
                date_min="2000-01-03",
                date_max="2000-01-04",
            ),
            dict(
                source="part",
                row_group=0,
                offset=2,
                rows=1,
                date_min="2001-01-02",
                date_max="2001-01-02",
            ),
        ]
    }
    objects, ref = a.build(entries, locations, {"part": source}, coverage_sha256="b" * 64)
    e = SimpleNamespace(
        SEC_ARCHIVE=Bucket(objects),
        ZION_PRIVATE_API_TOKEN="owner",
        CRSP_HISTORY_KEY=ref["key"],
        CRSP_HISTORY_SHA256=ref["sha256"],
    )
    rows = [
        dict(permno=10001, dlycaldt="2000-01-03", dlyprc="-10.25", dlyvol="10", dlyret=None),
        dict(permno=10001, dlycaldt="2000-01-04", dlyprc=None, dlyvol=None, dlyret="0.01"),
        dict(
            permno=10001, dlycaldt="2001-01-02", dlyprc="12.00000000001", dlyvol="15", dlyret="0.02"
        ),
    ]
    calls = []

    async def fetch(env, source, offset, count):
        calls.append((offset, count))
        return rows[offset : offset + count]

    monkeypatch.setattr(api, "fetch_rows", fetch)
    return e, ref, rows, calls


def query(env, p=None, token="owner", handler=None):
    req = SimpleNamespace(
        url="https://test/private/v1/stock-history?" + urlencode(p or {}),
        method="GET",
        headers={"authorization": "Bearer " + token},
    )
    return asyncio.run((handler or router.handle)(env, req))


def test_default_catalog_exposes_crsp_securities_and_historical_alias_search(monkeypatch):
    env, ref, _, calls = setup(monkeypatch)
    result, status = query(env, {"search": "OLD"})
    assert status == 200 and result["source"] == "WRDS_CRSP"
    assert result["security_count"] == 2 and {r["permno"] for r in result["symbols"]} == {
        10001,
        10002,
    }
    assert not calls


def test_stable_id_reads_real_source_spans_and_keeps_native_price_nulls(monkeypatch):
    env, ref, _, calls = setup(monkeypatch)
    result, status = query(env, {"permno": "10001", "limit": 2})
    assert status == 200 and len(result["prices"]) == 2
    assert result["prices"][0]["native_price"] == "-10.25"
    assert result["prices"][0]["price_magnitude"] == "10.25"
    assert result["prices"][0]["open"] is None and result["prices"][0]["adjusted_close"] is None
    assert result["prices"][1]["close"] is None
    assert result["next_cursor"] and calls == [(0, 2)]
    second, status = query(
        env,
        {
            "permno": "10001",
            "limit": 2,
            "cursor": result["next_cursor"],
            "expected_release_id": ref["release_id"],
        },
    )
    assert status == 200 and len(second["prices"]) == 1
    assert second["prices"][0]["close"] == "12.00000000001" and second["next_cursor"] is None


def test_ambiguous_reused_ticker_is_explicit_not_wrong_company(monkeypatch):
    env, ref, _, calls = setup(monkeypatch)
    result, status = query(env, {"symbol": "OLD"})
    assert status == 409 and result["error"]["code"] == "CRSP_AMBIGUOUS_TICKER" and not calls


def test_auth_release_dates_and_source_filters_fail_closed(monkeypatch):
    env, ref, _, calls = setup(monkeypatch)
    assert query(env, token="wrong")[1] == 401 and not env.SEC_ARCHIVE.reads
    assert query(env, {"as_of": "2001-01-01"})[1] == 422
    assert query(env, {"expected_release_id": "0" * 64})[1] == 409
    assert query(env, {"source": "invented"})[1] == 400
    result, status = query(
        env, {"permno": "10001", "date_from": "2001-01-01", "date_to": "2001-12-31"}
    )
    assert status == 200 and len(result["prices"]) == 1 and calls == [(2, 1)]


def test_source_wrong_permno_fails_not_false_join(monkeypatch):
    env, _, rows, _ = setup(monkeypatch)
    rows[0]["permno"] = 99999
    assert query(env, {"permno": "10001"})[1] == 503


def test_immutable_manifest_is_not_reloaded_for_every_catalog_page(monkeypatch):
    env, ref, _, _ = setup(monkeypatch)
    if hasattr(api, "_MANIFEST_CACHE"):
        monkeypatch.setattr(api, "_MANIFEST_CACHE", None)
    first, status = query(env, {"limit": 1})
    assert status == 200
    query(
        env, {"limit": 1, "cursor": first["next_cursor"], "expected_release_id": ref["release_id"]}
    )
    assert env.SEC_ARCHIVE.reads.count(ref["key"]) == 1
