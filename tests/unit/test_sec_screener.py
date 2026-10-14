import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/cloudflare-query/src"))
screen = importlib.import_module("sec_screener")


def row(value, end="2025-12-31", start="2025-01-01", metric="revenue"):
    return dict(
        metric=metric,
        value_decimal=value,
        period_start=start,
        period_end=end,
        concept="us-gaap:Revenues",
        currency="USD",
        unit="USD",
        source_observation_ids=[end],
        available_at="2026-02-01T05:00:00Z",
    )


def test_growth_exact_and_missing_or_changed_concepts():
    a = row("100000000000000000000000000000001", "2024-12-31", "2024-01-01")
    b = row("100000000000000000000000000000002")
    result = screen.company_row("0000000001", {"label": "X", "policy": {}}, [a, b])
    growth = result["metrics"]["revenue_growth"]
    assert growth["numerator"] == "1"
    assert growth["denominator"] == a["value_decimal"]
    assert len(growth["source_observation_ids"]) == 2
    a["concept"] = "other"
    assert "revenue_growth" not in screen.company_row("0000000001", {}, [a, b])["metrics"]


def test_filters_exact_sort_and_explicit_missing():
    index = {
        "companies": [
            screen.company_row("0000000001", {}, [row("0")]),
            screen.company_row("0000000002", {}, [row("2")]),
            screen.company_row("0000000003", {}, []),
        ]
    }
    rows, missing = screen.select(index, "revenue", "0", None, "desc", None)
    assert [r["cik"] for r in rows] == ["0000000002", "0000000001"]
    assert missing[0]["cik"] == "0000000003"
    with pytest.raises(ValueError):
        screen.select(index, "revenue", "NaN", None, "desc", None)


def test_quarantine_and_nonconsecutive_growth():
    data = {"policy": {"quarantined_metrics": {"revenue": "SECTOR_MAPPING_UNAPPROVED"}}}
    result = screen.company_row("0000000001", data, [row("5")])
    assert result["missing"]["revenue"] == "SECTOR_MAPPING_UNAPPROVED"
    assert "revenue" not in result["metrics"]
    result = screen.company_row("0000000001", {}, [row("1", "2023-12-31", "2023-01-01"), row("2")])
    assert "revenue_growth" not in result["metrics"]


def test_bounded_exponent_and_zero_denominator():
    with pytest.raises(ValueError):
        screen.exact("1e999999999")
    a = row("0", "2024-12-31", "2024-01-01")
    assert "revenue_growth" not in screen.company_row("0000000001", {}, [a, row("1")])["metrics"]


def test_private_handler_pins_cutoff_and_cursor(monkeypatch):
    import asyncio

    asyncio.run(_private_handler(monkeypatch))


async def _private_handler(monkeypatch):
    from types import SimpleNamespace

    from sec_engine_api import cursor_encode

    m = dict(
        release_id="release",
        registry_sha256="registry",
        maximum_as_of="2026-09-24T00:00:00Z",
        screening_index={"sha256": "index"},
    )
    index = dict(
        schema_version=screen.VERSION,
        release_id="release",
        registry_sha256="registry",
        as_of=m["maximum_as_of"],
        rights="PUBLIC_SEC_ONLY",
        licensed_sources_allowed=False,
        qualification="FIXED_SNAPSHOT",
        companies=[
            screen.company_row("0000000001", {}, [row("1")]),
            screen.company_row("0000000002", {}, [row("2")]),
        ],
    )

    async def root(env):
        return m

    async def artifact(env, ref, maximum):
        return index

    monkeypatch.setattr(screen, "root", root)
    monkeypatch.setattr(screen, "artifact", artifact)
    env = SimpleNamespace(ZION_PRIVATE_API_TOKEN="test-secret")

    class Headers:
        def __init__(self, token):
            self.token = token

        def get(self, key, default=None):
            return "Bearer " + self.token if key.lower() == "authorization" else default

    def request(query, token="test-secret", method="GET"):
        return SimpleNamespace(
            url="https://test/private/v1/financial-screen?" + query,
            method=method,
            headers=Headers(token),
        )

    _, status = await screen.handle(env, request("metric=revenue", token="wrong"))
    assert status == 401
    _, status = await screen.handle(env, request("metric=revenue", method="POST"))
    assert status == 405
    _, status = await screen.handle(env, request("metric=revenue&as_of=2025-01-01T00:00:00Z"))
    assert status == 422
    result, status = await screen.handle(env, request("metric=revenue&limit=1"))
    assert status == 200 and result["total_matches"] == 2 and result["next_cursor"]
    second, status = await screen.handle(
        env, request("metric=revenue&limit=1&cursor=" + result["next_cursor"])
    )
    assert status == 200 and second["matches"][0]["cik"] != result["matches"][0]["cik"]
    bad = cursor_encode({"query": "wrong", "release": "release", "offset": 1}, "test-secret")
    _, status = await screen.handle(env, request("metric=revenue&cursor=" + bad))
    assert status == 409
    _, status = await screen.handle(env, request("metric=revenue&sql=SELECT"))
    assert status == 400
