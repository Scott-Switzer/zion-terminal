import asyncio, importlib, sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
a = importlib.import_module("stock_latest_api")


def test_latest_daily_publication_is_exposed_without_replacing_crsp_history(monkeypatch):
    calls = []

    async def dispatch(env, path, params, rid):
        calls.append(params)
        return dict(
            release={
                "market_release_id": "a" * 64,
                "provider": "alpaca",
                "feed": "sip",
                "session": "2026-10-19",
            },
            coverage={"missing_symbols": []},
            page={"total": 1, "next_offset": None},
            data={
                "prices": [
                    {
                        "symbol": "TEST",
                        "session_date": "2026-10-19",
                        "close": "1.00001",
                        "available_at": None,
                    }
                ]
            },
        )

    monkeypatch.setattr(a, "market_dispatch", dispatch)
    e = SimpleNamespace(ZION_PRIVATE_API_TOKEN="owner")
    q = SimpleNamespace(
        method="GET",
        url="https://test/private/v1/stock-history?source=alpaca_latest&symbol=TEST",
        headers={"authorization": "Bearer owner"},
    )
    r = asyncio.run(a.execute(e, q))
    assert r["source"] == "ALPACA_SIP_DAILY" and r["prices"][0]["close"] == "1.00001"
    assert r["coverage"]["time_scope"] == "LATEST_VERIFIED_DAILY_PUBLICATION"
    assert calls[0]["q"] == "TEST"
