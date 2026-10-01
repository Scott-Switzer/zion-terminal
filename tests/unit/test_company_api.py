import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from company_api import company_dispatch
from contract_v2 import ContractError
from serving_v2 import ServingV2Error


class Obj:
    def __init__(self, value):
        self.value = value

    async def text(self):
        return self.value


class Bucket:
    def __init__(self, objects):
        self.objects = objects
        self.gets = []

    async def get(self, key):
        self.gets.append(key)
        value = self.objects.get(key)
        return Obj(value) if value is not None else None


def environment(*, market_symbol="BBAI", canonical_symbol="META"):
    serving_id = "serving-release-1"
    market_id = "a" * 64
    canonical_artifacts = {}
    artifacts = []

    def add(path, payload, *, storage_key=None):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        key = storage_key or f"gold/serving/releases/{serving_id}/{path}"
        canonical_artifacts[key] = raw.decode()
        artifacts.append({"path": path, "storage_key": key, "sha256": hashlib.sha256(raw).hexdigest()})

    artifact_path = f"entities/{canonical_symbol}"
    add("identity/resolver_index.json", {"symbols": {canonical_symbol: {"artifact_path": artifact_path, "entity": "issuer-1", "instrument": "instrument-1", "listing": "listing-1", "symbol": canonical_symbol}}})
    add("identity/entities.json", [{"entity_id": "issuer-1", "legal_name": "Test Issuer"}])
    add(f"{artifact_path}/snapshot.json", {"entity_id": "issuer-1", "symbol": canonical_symbol, "latest_price": None})
    add(f"{artifact_path}/fundamentals/annual.json", [{"metric_id": "revenue", "period_end": "2025-12-31", "period_type": "annual", "value_decimal": "100", "unit": "USD", "available_at": "2026-02-01T00:00:00Z", "observation_id": "f1"}])
    add(f"{artifact_path}/fundamentals/quarterly.json", [])
    add(f"{artifact_path}/prices/daily/2025.json", [{"session_date": "2025-12-31", "close": "10", "unit": "USD/share", "available_at": "2026-01-01T00:00:00Z"}])
    archive_release = "9b8c0acaad1d6dff807505b3"
    add("archive/index.json", [{"symbol": canonical_symbol, "pit_status": "SOURCE_LIMITED", "adjustment_status": "source_preserved_retrospective_adjustment_unknown"}])
    add(f"archive/{canonical_symbol}/prices/all.json", {"defaults": {"available_at": None, "source_id": "legacy-archive"}, "rows": [{"session_date": "2018-11-02", "close": "12.34"}]}, storage_key=f"gold/serving/releases/archive-compact-{archive_release}/archive/{canonical_symbol}/prices/all.json")
    # The archive index's immutable bytes are stored under the compact archive release too.
    index_artifact = artifacts[-2]
    archive_index_raw = canonical_artifacts[index_artifact["storage_key"]]
    index_artifact["storage_key"] = f"gold/serving/releases/archive-compact-{archive_release}/archive/index.json"
    canonical_artifacts[index_artifact["storage_key"]] = archive_index_raw
    manifest = {"schema_version": "financial-serving-v2", "serving_release_id": serving_id, "source": {"archive": {"pit_status": "SOURCE_LIMITED", "release_id": archive_release}, "fundamentals": {"snapshot_id": 10}, "prices": {"snapshot_id": 20}}, "artifacts": artifacts}
    manifest_raw = json.dumps(manifest).encode()
    pointer = {"serving_release_id": serving_id, "manifest_key": f"gold/serving/releases/{serving_id}/manifest.json", "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest()}
    market_data = {"gold/serving/CURRENT.json": json.dumps(pointer), pointer["manifest_key"]: manifest_raw.decode(), **canonical_artifacts}
    market_data["control/daily-prices/CURRENT.json"] = json.dumps({"version": market_id, "sha256": market_id, "data_key": f"gold/daily-prices/releases/{market_id}/data.json", "session": "2026-09-30", "provider": "alpaca", "feed": "sip", "published_at": "2026-10-01T12:00:00Z"})
    market_payload = {"session": "2026-09-30", "provider": "alpaca", "feed": "sip", "counts": {"target": 1}, "bars": [{"provider_symbol": market_symbol, "security_id": "market-security-1", "session_date": "2026-09-30", "close": "2.65"}]}
    # Keep the exact bytes consistent with the release identifier in the pointer.
    market_raw = json.dumps(market_payload, separators=(",", ":"))
    actual_market_id = hashlib.sha256(market_raw.encode()).hexdigest()
    market_data["control/daily-prices/CURRENT.json"] = json.dumps({"version": actual_market_id, "sha256": actual_market_id, "data_key": f"gold/daily-prices/releases/{actual_market_id}/data.json", "session": "2026-09-30", "provider": "alpaca", "feed": "sip", "published_at": "2026-10-01T12:00:00Z"})
    market_data[f"gold/daily-prices/releases/{actual_market_id}/data.json"] = market_raw
    return SimpleNamespace(MARKET_DATA=Bucket(market_data))


def test_current_packet_composes_canonical_fundamentals_and_separate_market_release():
    env = environment(canonical_symbol="BBAI", market_symbol="BBAI")
    result = asyncio.run(company_dispatch(env, "/v1/company/BBAI", {}, "r"))
    assert result["fundamentals"]["status"] == "AVAILABLE"
    assert result["prices"]["latest"]["observation"]["close"] == "2.65"
    assert result["releases"]["fundamentals"] == "serving-release-1"
    assert result["releases"]["market_price"] != result["releases"]["fundamentals"]
    assert result["prices"]["latest"]["freshness"]["available_at"] is None
    assert result["prices"]["latest"]["freshness"]["published_at"] == "2026-10-01T12:00:00Z"
    assert result["identity"]["market"]["security_id"] == "market-security-1"
    assert result["releases"]["archive"] == "9b8c0acaad1d6dff807505b3"
    assert result["prices"]["archive"]["observations"][0]["session_date"] == "2018-11-02"
    assert result["prices"]["archive"]["pit_status"] == "SOURCE_LIMITED"


def test_price_only_current_packet_is_partial_not_unsupported():
    env = environment(canonical_symbol="META", market_symbol="BBAI")
    result = asyncio.run(company_dispatch(env, "/v1/company/BBAI", {}, "r"))
    assert result["status"] == "PARTIAL"
    assert result["identity"]["market"]["security_id"] == "market-security-1"
    assert result["fundamentals"]["status"] == "DATA_NOT_PUBLISHED"
    assert result["prices"]["latest"]["status"] == "AVAILABLE"
    assert result["revisions"]["status"] == "DATA_NOT_PUBLISHED"


def test_historical_date_cutoff_includes_the_entire_utc_calendar_day():
    env = environment(canonical_symbol="META", market_symbol="BBAI")
    result = asyncio.run(company_dispatch(env, "/v1/company/META", {"as_of": "2026-02-01"}, "r"))
    assert result["fundamentals"]["annual"]
    assert result["releases"]["market_price"] is None
    assert not any(key.startswith("control/daily-prices/") for key in env.MARKET_DATA.gets)


def test_historical_packet_uses_pit_eligible_canonical_prices_without_current_feed():
    env = environment(canonical_symbol="META", market_symbol="BBAI")
    result = asyncio.run(company_dispatch(env, "/v1/company/META", {"as_of": "2026-06-01T00:00:00Z"}, "r"))
    assert result["prices"]["latest"]["status"] == "AVAILABLE"
    assert result["prices"]["latest"]["observation"]["session_date"] == "2025-12-31"
    assert result["releases"]["market_price"] is None
    assert result["prices"]["archive"]["observations"] == []
    assert not any(key.startswith("control/daily-prices/") for key in env.MARKET_DATA.gets)


def test_historical_packet_without_eligible_canonical_data_is_typed_unavailable():
    env = environment(canonical_symbol="META", market_symbol="BBAI")
    with pytest.raises(ContractError) as exc:
        asyncio.run(company_dispatch(env, "/v1/company/META", {"as_of": "2025-06-01T00:00:00Z"}, "r"))
    assert exc.value.code == "HISTORICAL_DATA_UNAVAILABLE"
    assert exc.value.status == 422
    assert not any(key.startswith("control/daily-prices/") for key in env.MARKET_DATA.gets)


def test_unsupported_security_is_a_typed_not_found():
    env = environment(canonical_symbol="META", market_symbol="BBAI")
    with pytest.raises(ContractError) as exc:
        asyncio.run(company_dispatch(env, "/v1/company/ZZZZ", {}, "r"))
    assert exc.value.code == "SECURITY_NOT_FOUND"


def test_company_endpoint_rejects_unknown_query_parameters():
    with pytest.raises(ContractError) as exc:
        asyncio.run(company_dispatch(environment(), "/v1/company/BBAI", {"source": "market"}, "r"))
    assert exc.value.code == "INVALID_ARGUMENT"


def test_current_packet_keeps_canonical_service_failure_explicit():
    class EmptyBucket:
        async def get(self, key):
            return None
    env = SimpleNamespace(MARKET_DATA=EmptyBucket())
    with pytest.raises(ContractError) as exc:
        asyncio.run(company_dispatch(env, "/v1/company/BBAI", {}, "r"))
    assert exc.value.code == "SECURITY_NOT_FOUND"
