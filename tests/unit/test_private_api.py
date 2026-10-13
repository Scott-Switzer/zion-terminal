import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from private_api import query_existing  # noqa: E402
from private_financials import PrivateFinancialsError  # noqa: E402
from serving_v2 import begin_telemetry, current_pointer_cache  # noqa: E402


class Obj:
    def __init__(self, raw):
        self.raw = raw

    async def text(self):
        return self.raw.decode()

    async def arrayBuffer(self):
        return self.raw


class Bucket:
    def __init__(self, objects):
        self.objects, self.keys = objects, []

    async def get(self, key):
        self.keys.append(key)
        return Obj(self.objects[key]) if key in self.objects else None


def make_env():
    rows = [
        dict(
            entity_id="issuer",
            metric_id="revenue",
            value_decimal=v,
            unit="USD",
            accounting_basis="us-gaap:Revenues",
            period_type="annual",
            period_start=f"{2020 + i}-01-01",
            period_end=f"{2020 + i}-12-31",
            available_at=f"{2021 + i}-02-01",
            evidence_id=f"e{i}",
        )
        for i, v in enumerate(["100", "110", "121", "133.1", "146.41", "161.051"])
    ]
    prefix = "gold/serving/releases/r1/"
    artifacts = {
        "identity/resolver_index.json": {
            "symbols": {"AAPL": {"artifact_path": "entities/a", "symbol": "AAPL"}}
        },
        "entities/a/snapshot.json": {"entity_id": "issuer", "symbol": "AAPL"},
        "entities/a/fundamentals/annual.json": rows,
        "entities/a/fundamentals/quarterly.json": [],
    }
    objects = {prefix + k: json.dumps(v).encode() for k, v in artifacts.items()}
    manifest = dict(
        schema_version="financial-serving-v2",
        serving_release_id="r1",
        artifacts=[
            {"path": k, "sha256": hashlib.sha256(objects[prefix + k]).hexdigest()}
            for k in artifacts
        ],
    )
    raw = json.dumps(manifest).encode()
    objects[prefix + "manifest.json"] = raw
    objects["gold/serving/CURRENT.json"] = json.dumps(
        dict(
            serving_release_id="r1",
            manifest_key=prefix + "manifest.json",
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
        )
    ).encode()
    current_pointer_cache().reset_for_tests()
    begin_telemetry()
    return SimpleNamespace(MARKET_DATA=Bucket(objects), CURRENT_POINTER_CACHE_TTL_MS=0)


def call(env, **body):
    return asyncio.run(query_existing(env, body, request_id="req"))


def test_pinned_query_identity_financials_and_growth():
    for operation in ["identity", "financials", "revenue_growth"]:
        env = make_env()
        result = call(env, operation=operation, symbol="aapl")
        assert result["entity"]["entity_id"] == "issuer"
        assert result["release"]["serving_release_id"] == "r1"
        assert env.MARKET_DATA.keys.count("gold/serving/CURRENT.json") == 1
        assert "gold/" not in json.dumps(result)
    assert result["data"]["cumulative_percent"] == "61.051"


def test_date_filters_future_revisions_and_preserves_available_at():
    env = make_env()
    result = call(env, operation="financials", symbol="AAPL", as_of="2024-03-01T00:00:00Z")
    assert len(result["data"]["observations"]) == 4
    assert result["data"]["observations"][-1]["value"] == "133.1"


def test_quarterly_request_does_not_return_annual_observations():
    result = call(make_env(), operation="financials", symbol="AAPL", period="quarterly")
    assert result["data"]["observations"] == []
    assert result["coverage"]["missing_reasons"] == ["METRIC_NOT_AVAILABLE:revenue"]


@pytest.mark.parametrize(
    "args,code",
    [
        ({"expected_release_id": "r2"}, "RELEASE_MISMATCH"),
        ({"extra": "not allowed"}, "INVALID_REQUEST"),
        ({"as_of": "bad"}, "INVALID_REQUEST"),
        ({"symbol": "../private"}, "INVALID_REQUEST"),
        ({"years": True}, "INVALID_REQUEST"),
        ({"metrics": ["revenue"] * 21}, "INVALID_REQUEST"),
        ({"operation": "fetch_provider"}, "INVALID_REQUEST"),
        ({"symbol": "NOPE"}, "ENTITY_NOT_FOUND"),
    ],
)
def test_request_errors_are_sanitized(args, code):
    with pytest.raises(PrivateFinancialsError) as e:
        call(make_env(), **({"operation": "financials", "symbol": "AAPL"} | args))
    assert e.value.code == code
    assert "gold/" not in e.value.message


def test_corruption_fails_closed_and_does_not_leak_path():
    env = make_env()
    env.MARKET_DATA.objects["gold/serving/releases/r1/entities/a/fundamentals/annual.json"] = b"[]"
    with pytest.raises(PrivateFinancialsError) as e:
        call(env, operation="financials", symbol="AAPL")
    assert e.value.status == 503
    assert "gold/" not in str(e.value)


def test_pointer_change_does_not_mix_releases():
    env = make_env()
    original = env.MARKET_DATA.get

    async def changing(key):
        result = await original(key)
        if key.endswith("manifest.json"):
            env.MARKET_DATA.objects["gold/serving/CURRENT.json"] = b"{}"
        return result

    env.MARKET_DATA.get = changing
    assert (
        call(env, operation="revenue_growth", symbol="AAPL")["release"]["serving_release_id"]
        == "r1"
    )


def replace_artifact(env, path, value):
    prefix = "gold/serving/releases/r1/"
    raw = json.dumps(value).encode()
    env.MARKET_DATA.objects[prefix + path] = raw
    mkey = prefix + "manifest.json"
    manifest = json.loads(env.MARKET_DATA.objects[mkey])
    for entry in manifest["artifacts"]:
        if entry["path"] == path:
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
    mraw = json.dumps(manifest).encode()
    env.MARKET_DATA.objects[mkey] = mraw
    pointer = json.loads(env.MARKET_DATA.objects["gold/serving/CURRENT.json"])
    pointer["manifest_sha256"] = hashlib.sha256(mraw).hexdigest()
    env.MARKET_DATA.objects["gold/serving/CURRENT.json"] = json.dumps(pointer).encode()


def test_known_symbol_outside_identity_validity_cannot_fall_back():
    env = make_env()
    replace_artifact(
        env,
        "identity/resolver_index.json",
        {
            "symbols": {
                "AAPL": {"artifact_path": "entities/a", "valid_from": "2025-01-01T00:00:00Z"}
            }
        },
    )
    with pytest.raises(PrivateFinancialsError) as e:
        call(env, operation="identity", symbol="AAPL", as_of="2024-01-01T00:00:00Z")
    assert e.value.code == "ENTITY_NOT_AVAILABLE_AT_DATE"


def test_conflicting_same_revision_never_selects_arbitrarily():
    env = make_env()
    path = "entities/a/fundamentals/annual.json"
    rows = json.loads(env.MARKET_DATA.objects["gold/serving/releases/r1/" + path])
    rows.append({**rows[-1], "value_decimal": "999"})
    replace_artifact(env, path, rows)
    with pytest.raises(PrivateFinancialsError) as e:
        call(env, operation="financials", symbol="AAPL")
    assert e.value.code == "AMBIGUOUS_OBSERVATION"
