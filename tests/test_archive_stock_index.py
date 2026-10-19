import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/cloudflare-query/src"))
import archive_stock_index as a


def test_provider_native_identity_is_qualified_and_year_spans_bounded():
    spans = a.spans([7, 7, 7], ["2020-01-01", "2020-02-01", "2021-01-01"], "yfinance", "0")
    assert [(s["rows"], s["date_min"]) for s in spans] == [(2, "2020-01-01"), (1, "2021-01-01")]
    assert a.identity("yfinance", 7) != a.identity("simfin", 7)


def test_native_adjusted_field_keeps_unknown_request_basis():
    p = a.point(
        dict(
            security_id=7,
            price_date="2020-01-01T00:00:00Z",
            close_price="4.5",
            adjusted_close_price="3.5",
            volume=None,
        ),
        "yfinance",
    )
    assert p["close"] == "4.5" and p["adjusted_close"] == "3.5"
    assert p["price_basis"] == "UNKNOWN_FOR_STORED_ROWS"
    assert p["available_at"] is None


def test_provider_api_fetch_uses_financial_store_and_pins_etag(monkeypatch):
    import asyncio
    import json
    import types

    import archive_stock_history_api as api

    class Request:
        def __init__(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs
            self.js_object = self

    monkeypatch.setitem(sys.modules, "workers", types.SimpleNamespace(Request=Request))

    class Response:
        status = 200

        async def text(self):
            return json.dumps(
                dict(
                    source=dict(
                        store="financial", path="estate/synthetic.parquet", etag="abc", bytes=10
                    ),
                    data=dict(rows=[dict(security_id=7)]),
                )
            )

    class Binding:
        async def fetch(self, request):
            assert "/financial/rows?" in request.url
            assert request.kwargs["headers"]["if-match"] == '"abc"'
            return Response()

    rows = asyncio.run(
        api.fetch_rows(
            types.SimpleNamespace(PRIVATE_DATASETS=Binding()),
            dict(key="estate/synthetic.parquet", etag='"abc"', bytes=10, rows=1),
            0,
            1,
        )
    )
    assert rows[0]["security_id"] == 7


def test_api_returns_provider_qualified_history(monkeypatch):
    import asyncio
    import types

    import archive_stock_history_api as api

    entry = dict(
        provider="yfinance",
        security_id="7",
        symbol="SYN",
        aliases=["SYN"],
        name="Synthetic",
        normalized_rows=1,
        date_min="2020-01-01",
        date_max="2020-01-01",
        fragments=[
            dict(source="0", offset=0, rows=1, date_min="2020-01-01", date_max="2020-01-01")
        ],
    )
    objects, ptr = a.build(
        {"yfinance:7": entry},
        {"0": dict(key="estate/synthetic.parquet", etag="abc", bytes=10, rows=1)},
    )

    class Object:
        def __init__(self, raw):
            self.raw = raw
            self.size = len(raw)

        async def text(self):
            return self.raw.decode()

    class Store:
        async def get(self, key):
            return Object(objects[key])

    async def rows(*args):
        return [
            dict(security_id=7, source_name="yfinance", price_date="2020-01-01", close_price=4.5)
        ]

    monkeypatch.setattr(api, "authenticate", lambda *args: None)
    monkeypatch.setattr(api, "fetch_rows", rows)
    env = types.SimpleNamespace(
        MARKET_DATA=Store(), PROVIDER_HISTORY_KEY=ptr["key"], PROVIDER_HISTORY_SHA256=ptr["sha256"]
    )
    request = types.SimpleNamespace(
        method="GET", url="https://example/api/v1/stocks?source=yfinance&security_id=7"
    )
    result = asyncio.run(api.execute(env, request))
    assert result["prices"][0]["close"] == "4.5"
    assert result["prices"][0]["provider"] == "yfinance"
    assert result["symbol"] == "SYN"
    assert result["security_id"] == "7"
