import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/cloudflare-query/src"))


def module():
    assert importlib.util.find_spec("sec_universe_screen") is not None, (
        "Streaming screener required"
    )
    return importlib.import_module("sec_universe_screen")


def company(cik, value=None):
    return {
        "cik": str(cik).zfill(10),
        "label": "SEC issuer",
        "metrics": {"revenue": {"value_decimal": value, "unit": "USD"}}
        if value is not None
        else {},
        "missing": {"revenue": "NO_VALID_ANNUAL_OBSERVATION"},
    }


@pytest.fixture
def fixture(monkeypatch):
    screen = module()
    manifest = {
        "release_id": "release",
        "registry_sha256": "registry",
        "maximum_as_of": "2026-09-24T00:00:00Z",
        "screening_metrics": {
            "schema_version": "sec-screen-metric-shards-v1",
            "metrics": {
                "revenue": [{"key": "a", "sha256": "a" * 64}, {"key": "b", "sha256": "b" * 64}]
            },
            "qualification": "FIXED_SEC_SNAPSHOT",
        },
    }
    chunks = {
        "a": {
            "release_id": "release",
            "registry_sha256": "registry",
            "companies": [company(1, "9007199254740993"), company(2, "9007199254740994")],
        },
        "b": {
            "release_id": "release",
            "registry_sha256": "registry",
            "companies": [company(3, "9007199254740994"), company(4)],
        },
    }
    reads = []

    async def root(env):
        return manifest

    async def artifact(env, ref, maximum):
        reads.append(ref["key"])
        return chunks[ref["key"]]

    monkeypatch.setattr(screen, "root", root)
    monkeypatch.setattr(screen, "artifact", artifact)
    env = SimpleNamespace(ZION_PRIVATE_API_TOKEN="test-secret")

    def query(params=None, token="test-secret", method="GET"):
        request = SimpleNamespace(
            url="https://test/private/v1/financial-screen?" + urlencode(params or {}),
            method=method,
            headers={"authorization": "Bearer " + token},
        )
        return asyncio.run(screen.handle(env, request))

    return screen, manifest, chunks, reads, query


def test_streaming_exact_pages_missing_and_desc_ties(fixture):
    screen, manifest, chunks, reads, query = fixture
    first, status = query(
        {"metric": "revenue", "sort": "desc", "limit": "1", "minimum": "9007199254740994"}
    )
    assert status == 200
    assert first["total_matches"] == 2
    assert first["matches"][0]["cik"] == "0000000003"
    assert reads == ["b", "a"]
    assert first["missing_companies"] == [
        {"cik": "0000000004", "reason": "NO_VALID_ANNUAL_OBSERVATION"}
    ]
    second, status = query(
        {
            "metric": "revenue",
            "sort": "desc",
            "limit": "1",
            "minimum": "9007199254740994",
            "cursor": first["next_cursor"],
        }
    )
    assert status == 200 and second["matches"][0]["cik"] == "0000000002"
    assert second["next_cursor"] is None
    assert first["limits"]["archive_bytes_read"] == 0


def test_unknown_cik_release_and_cursor_query_rejected(fixture):
    _, _, _, _, query = fixture
    assert query({"metric": "revenue", "ciks": "0000000999"})[1] == 404
    assert query({"metric": "revenue", "expected_release_id": "other"})[1] == 409
    page, _ = query({"metric": "revenue", "limit": "1"})
    assert query({"metric": "revenue", "minimum": "2", "cursor": page["next_cursor"]})[1] == 409


def test_auth_bounds_source_version_and_lexical_threshold(fixture, monkeypatch):
    screen, manifest, chunks, reads, query = fixture
    assert query({"metric": "revenue"}, token="wrong")[1] == 401
    assert not reads
    assert query({"metric": "revenue"}, method="POST")[1] == 405
    assert query({"metric": "revenue", "minimum": "1e99999999"})[1] == 422
    assert not reads
    chunks["b"]["release_id"] = "changed"
    assert query({"metric": "revenue"})[1] == 409
    chunks["b"]["release_id"] = "release"
    monkeypatch.setattr(screen, "MAX_INDEX_BYTES", 10)
    assert query({"metric": "revenue"})[1] == 413


def test_chunk_limit_and_unsorted_source_reject(fixture, monkeypatch):
    screen, manifest, chunks, reads, query = fixture
    monkeypatch.setattr(screen, "MAX_CHUNKS", 1)
    assert query({"metric": "revenue"})[1] == 413
    monkeypatch.setattr(screen, "MAX_CHUNKS", 32)
    chunks["a"]["companies"].reverse()
    assert query({"metric": "revenue", "sort": "asc"})[1] == 409


def test_full_twenty_thousand_cik_set_supported(fixture):
    _, manifest, chunks, _, query = fixture
    # Missing rows are still materialized universe members, never zero matches.
    chunks["a"]["companies"] = [company(i) for i in range(1, 10001)]
    chunks["b"]["companies"] = [company(i) for i in range(10001, 20001)]
    result, status = query(
        {
            "metric": "revenue",
            "sort": "asc",
            "ciks": ",".join(str(i).zfill(10) for i in range(1, 20001)),
        }
    )
    assert status == 200 and result["universe_companies"] == 20000
    assert len(result["missing_companies"]) == 20000 and result["total_matches"] == 0


def test_legacy_manifest_delegates(fixture, monkeypatch):
    screen, manifest, _, reads, query = fixture
    manifest.pop("screening_metrics")

    async def legacy(env, request):
        return {"legacy": True}, 200

    monkeypatch.setattr(screen.legacy, "handle", legacy)
    assert query({"metric": "revenue"}) == ({"legacy": True}, 200)
    assert not reads


def test_declared_read_budget_rejects_before_loading_chunks(fixture, monkeypatch):
    screen, manifest, chunks, reads, query = fixture
    for ref in manifest["screening_metrics"]["metrics"]["revenue"]:
        ref["bytes"] = 1000
    monkeypatch.setattr(screen, "MAX_INDEX_BYTES", 1500)
    result, status = query({"metric": "revenue"})
    assert status == 413 and result["error"]["code"] == "SCREEN_INDEX_BUDGET_EXCEEDED"
    assert not reads


def test_missing_response_budget_and_duplicate_cik_reject(fixture, monkeypatch):
    screen, _, chunks, _, query = fixture
    monkeypatch.setattr(screen, "MAX_RESPONSE", 10)
    assert query({"metric": "revenue"})[1] == 413
    monkeypatch.setattr(screen, "MAX_RESPONSE", 2 * 1048576)
    chunks["b"]["companies"] = [company(2, "9007199254740994")]
    result, status = query({"metric": "revenue"})
    assert status == 409 and result["error"]["code"] == "SCREEN_SOURCE_DUPLICATE_OR_INVALID_CIK"
