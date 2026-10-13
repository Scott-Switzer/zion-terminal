from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy" / "cloudflare-query" / "src"))

from contract_v2 import ContractError
from contract_v3 import CONTRACT_VERSION, TOOLS, contract_sha256
from synthetic_v3 import SyntheticReleaseAdapter, SyntheticReleaseError

# The Cloudflare workers package imports the runtime-only ``js`` module. These
# bridge tests call the pure async dispatch method, so provide its two class
# symbols without importing the runtime adapter.
if "workers" not in sys.modules:
    sys.modules["workers"] = types.SimpleNamespace(Response=object, WorkerEntrypoint=object)
from thin_worker import Default


class _Obj:
    def __init__(self, value: bytes):
        self.value = value

    async def text(self):
        return self.value.decode()


class _Bucket:
    def __init__(self, objects):
        self.objects = objects

    async def get(self, key):
        value = self.objects.get(key)
        return _Obj(value) if value is not None else None


class _Env:
    SYNTHETIC_WORLD_ID = "world-1"
    SYNTHETIC_WORLD_IDS = "world-1"
    SYNTHETIC_CURRENT_KEY = "control/synthetic-worlds/world-1/CURRENT.json"

    def __init__(self, objects):
        self.MARKET_DATA = _Bucket(objects)


def _release(*, hidden: bool = False):
    world = {"world_type": "synthetic", "world_id": "world-1", "version": "world-v2-1-1y", "generator": "test", "seed": 1}
    public = {
        "public/entities.json": {"world": world, "entities": [{"entity_id": "synthetic:company:NOVA", "symbol": "NOVA", "display_name": "Nova", "world": world}]},
        "public/financials.json": {"world": world, "observations": [{"entity_id": "synthetic:company:NOVA", "metric": "revenue", "value": 10, "unit": "USD", "period": "2026Q1", "period_end": "2026-03-31", "observation_at": "2026-03-31T00:00:00Z", "available_at": "2026-04-30T00:00:00Z", "world": world, "provenance": {"source_record": "q1"}}]},
        "public/prices.json": {"world": world, "prices": [{"entity_id": "synthetic:company:NOVA", "security": "NOVA", "session": "2026-04-30", "observation_at": "2026-04-30T00:00:00Z", "available_at": "2026-04-30T00:00:00Z", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 10, "world": world}]},
        "public/events.json": {"world": world, "events": []},
    }
    if hidden:
        public["public/financials.json"]["hidden_truth"] = {"latents": [1]}
    qc = {"status": "PASS", "world_id": "world-1", "world_version": world["version"], "validator_version": "test"}
    qc_raw = json.dumps(qc, sort_keys=True, separators=(",", ":")).encode()
    hashes = {}
    objects = {}
    for name, value in public.items():
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        objects[name] = raw
        hashes[name] = {"sha256": hashlib.sha256(raw).hexdigest()}
    manifest = {"schema_version": "1", "world": world, "producer": {"name": "test", "git_sha": "abc"}, "qc_status": "PASS", "qc_report_sha256": hashlib.sha256(qc_raw).hexdigest(), "qc_validator_version": "test", "artifact_hashes": hashes}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    release_id = hashlib.sha256(manifest_raw).hexdigest()
    prefix = f"gold/synthetic-worlds/releases/{release_id}"
    objects[f"{prefix}/manifest.json"] = manifest_raw
    objects[f"{prefix}/qc_certification.json"] = qc_raw
    for name, raw in list(objects.items()):
        if name.startswith("public/"):
            objects[f"{prefix}/{name}"] = raw
            del objects[name]
    objects["control/synthetic-worlds/world-1/CURRENT.json"] = json.dumps({"world_id": "world-1", "version": world["version"], "prefix": prefix, "release_id": release_id}, sort_keys=True).encode()
    return _Env(objects), world, release_id


def test_v3_manifest_is_hash_pinned_and_world_capable():
    manifest = json.loads((Path(__file__).parents[2] / "contracts" / "zion-tool-contract-v3.json").read_text())
    sidecar = (Path(__file__).parents[2] / "contracts" / "zion-tool-contract-v3.sha256").read_text().strip()
    from contract_v3 import canonical_manifest
    assert manifest == canonical_manifest()
    assert sidecar == contract_sha256()
    assert CONTRACT_VERSION == "zion-tool-contract-v3"
    assert len(TOOLS) == 11
    assert all(item["supported_worlds"] == ["real", "synthetic"] for item in TOOLS.values())
    assert len(contract_sha256()) == 64


class _RealV3Harness:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def _tool_v2(self, name, body, rid):
        self.calls.append((name, body, rid))
        return self.result


def _run_real_v3(name, arguments, result, *, as_of=None, release="real-release"):
    harness = _RealV3Harness(result)
    body = {
        "world": {"world_type": "real", "world_id": "us-public-markets"},
        "arguments": arguments,
        "serving_release_id": release,
    }
    if as_of is not None:
        body["as_of"] = as_of
    response = asyncio.run(Default._tool_v3(harness, name, body, "req-real"))
    return harness, response


def test_real_v3_pit_keeps_as_of_in_v2_envelope_and_pins_release():
    harness, response = _run_real_v3(
        "get_fundamentals",
        {"symbol": "MSFT", "metrics": ["revenue"], "period": "annual", "lookback": 8},
        {
            "data": {"observations": [{"period": "2023-06-30"}]},
            "evidence": [{"period": "2023-06-30"}],
            "quality": {"status": "VERIFIED"},
            "release": {},
        },
        as_of="2024-01-01T00:00:00Z",
    )
    _, v2_body, _ = harness.calls[0]
    assert v2_body["as_of"] == "2024-01-01T00:00:00Z"
    assert "as_of" not in v2_body["arguments"]
    assert response["release"]["serving_release_id"] == "real-release"
    assert response["data"]["observations"][0]["period"] == "2023-06-30"


def test_real_v3_compare_preserves_pinned_release():
    _, response = _run_real_v3(
        "compare",
        {"entities": ["MSFT", "NVDA", "TSLA"], "metric": "operating_margin"},
        {"data": {"series": []}, "evidence": [], "quality": {"status": "VERIFIED"}, "release": {}},
    )
    assert response["release"]["serving_release_id"] == "real-release"


def test_real_v3_calculate_preserves_exact_result_and_pinned_release():
    _, response = _run_real_v3(
        "calculate",
        {"operation": "percent_change", "values": ["10", "12"]},
        {"data": {"value": "20.00", "operation": "percent_change"}, "evidence": [], "quality": {"status": "CALCULATED"}, "release": {}},
    )
    assert response["data"]["value"] == "20.00"
    assert response["release"]["serving_release_id"] == "real-release"


def test_real_v3_release_mismatch_fails_closed():
    with pytest.raises(ContractError, match="V2 result release differs") as error:
        _run_real_v3(
            "compare",
            {"entities": ["MSFT", "NVDA"], "metric": "revenue"},
            {"data": {}, "evidence": [], "quality": {}, "release": {"serving_release_id": "other-release"}},
        )
    assert error.value.code == "RELEASE_MISMATCH"
    assert error.value.status == 502


def test_public_release_loads_and_filters_pit():
    async def check():
        env, world, release_id = _release()
        release = await SyntheticReleaseAdapter(env).load(world)
        assert release.release_id == release_id
        with pytest.raises(SyntheticReleaseError, match="not available"):
            release.fundamentals({"symbol": "NOVA", "metrics": ["revenue"], "as_of": "2026-04-01T00:00:00Z"})
        assert len(release.fundamentals({"symbol": "NOVA", "metrics": ["revenue"], "as_of": "2026-05-01T00:00:00Z"})) == 1
        row = release.fundamentals({"symbol": "NOVA", "metrics": ["revenue"]})[0]
        assert row["evidence_id"].startswith("synthetic:financial:")
    asyncio.run(check())


def test_hidden_public_payload_is_rejected():
    async def check():
        env, world, _ = _release(hidden=True)
        with pytest.raises(SyntheticReleaseError, match="hidden-only"):
            await SyntheticReleaseAdapter(env).load(world)
    asyncio.run(check())


def test_world_and_release_mismatches_fail_closed():
    async def check():
        env, _, release_id = _release()
        with pytest.raises(SyntheticReleaseError) as unknown:
            await SyntheticReleaseAdapter(env).load({"world_type": "synthetic", "world_id": "other"})
        assert unknown.value.code == "WORLD_NOT_FOUND"
        with pytest.raises(SyntheticReleaseError) as pinned:
            await SyntheticReleaseAdapter(env).load({"world_type": "synthetic", "world_id": "world-1"}, pinned_release_id="0" * 64)
        assert pinned.value.code == "SYNTHETIC_RELEASE_UNAVAILABLE"
        assert release_id
    asyncio.run(check())
