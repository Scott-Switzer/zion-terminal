import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
a = importlib.import_module("stock_archive")
api = importlib.import_module("stock_history_api")


def body(close="1.500000000000000001"):
    return (
        '{"bars":{"TEST":[{"t":"2020-01-02T05:00:00Z","o":1.1,"h":2,"l":1,"c":'
        + close
        + ',"v":10,"vw":null,"n":2}]}}'
    ).encode()


def test_original_decimal_and_source_qualification_survive_without_fake_pit():
    rows, gaps = a.parse_page(body(), "TEST")
    assert rows[0]["close"] == "1.500000000000000001"
    assert rows[0]["vwap"] is None and not gaps
    assert rows[0]["session_date"] == "2020-01-02"
    _, gaps = a.parse_page(body("3"), "TEST")
    assert gaps[0]["reason"] == "INVALID_OHLC"


class Object:
    def __init__(self, raw):
        self.raw = raw
        self.size = len(raw)

    async def text(self):
        return self.raw.decode()


class Bucket:
    def __init__(self, objects):
        self.objects = objects
        self.reads = []

    async def get(self, key):
        self.reads.append(key)
        return Object(self.objects[key]) if key in self.objects else None


def env():
    raw = body()
    sha = a.digest(raw)
    key = "raw/alpaca/equity-bars/test.json"
    pages = {
        sha: dict(
            key=key,
            sha256=sha,
            bytes=len(raw),
            currency="USD",
            feed="SIP",
            price_basis="RAW",
            adjustment_method="NONE",
        )
    }
    index = {
        "TEST": dict(
            source_pages=[sha],
            normalized_rows=1,
            date_min="2020-01-02",
            date_max="2020-01-02",
            asset_ids=["observed-current-id"],
        )
    }
    objects, ref = a.build(index, pages, inventory_sha256="a" * 64, normalized_rows=1)
    objects[key] = raw
    return SimpleNamespace(
        MARKET_DATA=Bucket(objects),
        ZION_PRIVATE_API_TOKEN="token",
        STOCK_HISTORY_KEY=ref["key"],
        STOCK_HISTORY_SHA256=ref["sha256"],
    ), ref


def query(e, params=None, token="token"):
    return asyncio.run(
        api.handle(
            e,
            SimpleNamespace(
                url="https://test/private/v1/stock-history?" + urlencode(params or {}),
                method="GET",
                headers={"authorization": "Bearer " + token},
            ),
        )
    )


def test_full_source_index_is_not_gated_by_fundamental_or_ticker_cohort():
    e, ref = env()
    catalog, status = query(e)
    assert status == 200 and catalog["symbols"][0]["symbol"] == "TEST"
    assert len(e.MARKET_DATA.reads) == 1
    result, status = query(e, {"symbol": "TEST", "expected_release_id": ref["release_id"]})
    assert status == 200 and result["prices"][0]["close"] == "1.500000000000000001"
    assert result["prices"][0]["available_at"] is None
    assert result["prices"][0]["security_id"] == "alpaca:sip:source-symbol:TEST"
    assert result["coverage"]["historical_identity"] == "NOT_ESTABLISHED"
    assert result["prices"][0]["source_ids"]


def test_auth_hash_and_asof_fail_closed():
    e, ref = env()
    assert query(e, token="bad")[1] == 401 and not e.MARKET_DATA.reads
    assert query(e, {"symbol": "TEST", "as_of": "2020-01-03"})[1] == 422
    assert query(e, {"symbol": "../bad"})[1] == 400
    assert query(e, {"expected_release_id": "0" * 64})[1] == 409
    raw_key = "raw/alpaca/equity-bars/test.json"
    e.MARKET_DATA.objects[raw_key] += b" "
    assert query(e, {"symbol": "TEST"})[1] == 503


def test_rejected_bar_gaps_respect_requested_dates():
    e, ref = env()
    key = "raw/alpaca/equity-bars/test.json"
    # Construct a new correctly pinned source whose bar is rejected by financial validation.
    raw = body("3")
    sha = a.digest(raw)
    pages = {
        sha: dict(
            key=key,
            sha256=sha,
            bytes=len(raw),
            currency="USD",
            feed="SIP",
            price_basis="RAW",
            adjustment_method="NONE",
        )
    }
    index = {
        "TEST": dict(
            source_pages=[sha],
            normalized_rows=1,
            date_min="2020-01-02",
            date_max="2020-01-02",
            asset_ids=[],
        )
    }
    objects, ref = a.build(index, pages, inventory_sha256="a" * 64, normalized_rows=1)
    objects[key] = raw
    e.MARKET_DATA = Bucket(objects)
    e.STOCK_HISTORY_KEY = ref["key"]
    e.STOCK_HISTORY_SHA256 = ref["sha256"]
    result, status = query(e, {"symbol": "TEST", "date_from": "2021-01-01"})
    assert status == 200 and result["gaps"] == []
    result, status = query(e, {"symbol": "TEST"})
    assert status == 200 and result["gaps"][0]["source_ids"]
    assert all(sid in result["source_lineage"] for sid in result["gaps"][0]["source_ids"])
