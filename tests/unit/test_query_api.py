import json
from pathlib import Path

from fastapi.testclient import TestClient

from zion_terminal.api import create_app


def _real_release(path: Path) -> None:
    path.write_text(json.dumps({
        "world": {"world_type": "real", "world_id": "us-public-markets", "version": "release-a"},
        "entity": {"entity_id": "real:equity:1", "display_name": "Apple Inc.", "symbol": "AAPL"},
        "evidence": [
            {"entity_id": "real:equity:1", "metric": "revenue", "value": 100.0, "unit": "USD", "period": "FY2025", "observation_at": "2025-09-27T00:00:00Z", "available_at": "2025-10-31T00:00:00Z", "retrieved_at": "2026-09-15T00:00:00Z", "source": "PPE", "source_record": "sec:revenue", "world": {"world_type": "real", "world_id": "us-public-markets", "version": "release-a"}, "provenance": {"release_id": "release-a"}, "quality": {"status": "observed"}},
            {"entity_id": "real:equity:1", "metric": "operating_margin", "value": 0.3, "unit": "ratio", "period": "FY2025", "observation_at": "2025-09-27T00:00:00Z", "available_at": "2025-10-31T00:00:00Z", "retrieved_at": "2026-09-15T00:00:00Z", "source": "PPE", "source_record": "sec:margin", "world": {"world_type": "real", "world_id": "us-public-markets", "version": "release-a"}, "provenance": {"release_id": "release-a"}, "quality": {"status": "calculated"}},
            {"entity_id": "real:equity:1", "metric": "last_price", "value": 200.0, "unit": "USD/share", "period": "2026-01-01", "observation_at": "2026-01-01T21:00:00Z", "available_at": "2026-01-01T21:00:00Z", "retrieved_at": "2026-09-15T00:00:00Z", "source": "PPE", "source_record": "market:close", "world": {"world_type": "real", "world_id": "us-public-markets", "version": "release-a"}, "provenance": {"release_id": "release-a"}, "quality": {"status": "observed"}},
        ],
        "release": {"release_id": "release-a", "qc_status": "VERIFIED"},
    }))


def _synthetic_release(root: Path, qc_status: str = "PASS") -> Path:
    (root / "public").mkdir(parents=True)
    (root / "hidden").mkdir()
    world = {"world_type": "synthetic", "world_id": "test-world-001", "version": "native-a"}
    entities = [{"entity_id": "synthetic:company:NOVA", "symbol": "NOVA", "display_name": "Nova", "sector": "Software", "world": world}]
    financials = [{"entity_id": "synthetic:company:NOVA", "metric": "revenue", "value": 100.0, "unit": "USD", "period": "FY2025", "observation_at": "2025-12-31T00:00:00Z", "available_at": "2026-02-15T00:00:00Z", "retrieved_at": "2026-02-15T00:00:00Z", "source": "market-fuzzer-native", "source_record": "accounting:NOVA", "calculation": None, "world": world, "provenance": {"producer": {"git_sha": "abc"}}, "quality": {"status": "observed"}}, {"entity_id": "synthetic:company:NOVA", "metric": "operating_margin", "value": 0.2, "unit": "ratio", "period": "FY2025", "observation_at": "2025-12-31T00:00:00Z", "available_at": "2026-02-15T00:00:00Z", "retrieved_at": "2026-02-15T00:00:00Z", "source": "market-fuzzer-native", "source_record": "accounting:NOVA", "calculation": "operating_income / revenue", "world": world, "provenance": {"producer": {"git_sha": "abc"}}, "quality": {"status": "calculated"}}]
    prices = [{"security": "NOVA", "session": "2026-02-16", "observation_at": "2026-02-16T20:00:00Z", "available_at": "2026-02-16T20:00:00Z", "open": 9.0, "high": 11.0, "low": 8.0, "close": 10.0, "volume": 100, "unit": "USD/share", "provenance": {"source_record": "session:NOVA", "producer": {"git_sha": "abc"}}}]
    (root / "public/entities.json").write_text(json.dumps({"world": world, "entities": entities}))
    (root / "public/financials.json").write_text(json.dumps({"world": world, "observations": financials}))
    (root / "public/prices.json").write_text(json.dumps({"world": world, "prices": prices}))
    (root / "manifest.json").write_text(json.dumps({"world": world, "producer": {"git_sha": "abc"}, "artifact_hashes": {"public/prices.json": {"sha256": "not-used"}}}))
    (root / "qc.json").write_text(json.dumps({"status": qc_status, "world_id": "test-world-001", "world_version": "native-a"}))
    return root


def test_http_real_and_synthetic_have_same_contract(tmp_path: Path):
    real = tmp_path / "real.json"
    _real_release(real)
    synthetic = _synthetic_release(tmp_path / "synthetic")
    app = create_app(real_file=real, synthetic_dir=synthetic, qc_report=synthetic / "qc.json")
    client = TestClient(app)
    payloads = [
        {"query": "Give me revenue, operating margin and last price for AAPL", "world": {"world_type": "real", "world_id": "us-public-markets"}},
        {"query": "Give me revenue, operating margin and last price for NOVA", "world": {"world_type": "synthetic", "world_id": "test-world-001"}},
    ]
    responses = [client.post("/v1/query", json=payload) for payload in payloads]
    assert all(response.status_code == 200 for response in responses)
    assert set(responses[0].json()) == set(responses[1].json())
    assert responses[0].json()["quality"]["llm_required"] is False
    assert responses[1].json()["release"]["qc_status"] == "PASS"


def test_typed_errors_and_health_capabilities(tmp_path: Path):
    real = tmp_path / "real.json"
    _real_release(real)
    client = TestClient(create_app(real_file=real))
    assert client.get("/healthz").json()["status"] == "ok"
    assert "revenue" in client.get("/v1/capabilities").json()["metrics"]
    response = client.post("/v1/query", json={"query": "Give me EBITDA for AAPL", "world": {"world_type": "real", "world_id": "us-public-markets"}}, headers={"x-request-id": "test-request"})
    assert response.status_code == 422
    assert response.json()["error"] == {"request_id": "test-request", "code": "QUERY_NOT_SUPPORTED", "message": "supported metrics are revenue, operating margin, and last price", "retryable": False}
    response = client.post("/v1/query", json={"query": "Give me revenue for MSFT", "world": {"world_type": "real", "world_id": "us-public-markets"}})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ENTITY_NOT_FOUND"


def test_uncertified_synthetic_release_is_rejected(tmp_path: Path):
    synthetic = _synthetic_release(tmp_path / "synthetic", qc_status="FAIL")
    client = TestClient(create_app(synthetic_dir=synthetic, qc_report=synthetic / "qc.json"))
    response = client.post("/v1/query", json={"query": "NOVA revenue", "world": {"world_type": "synthetic", "world_id": "test-world-001"}})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WORLD_NOT_CERTIFIED"


def test_as_of_rejects_evidence_not_yet_available(tmp_path: Path):
    real = tmp_path / "real.json"
    _real_release(real)
    client = TestClient(create_app(real_file=real))
    response = client.post(
        "/v1/query",
        json={
            "query": "AAPL revenue",
            "world": {"world_type": "real", "world_id": "us-public-markets"},
            "as_of": "2025-10-01T00:00:00Z",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "METRIC_NOT_AVAILABLE"


def test_public_path_escape_is_rejected(tmp_path: Path):
    synthetic = _synthetic_release(tmp_path / "synthetic")
    from zion_terminal.truth_query import SyntheticReleaseClient

    client = SyntheticReleaseClient(synthetic, synthetic / "qc.json")
    try:
        client._inside_public("../hidden/world_state.json")
    except Exception as error:
        assert getattr(error, "code", None) == "INVALID_UPSTREAM_RESPONSE"
    else:
        raise AssertionError("hidden path unexpectedly accepted")


def test_real_release_is_pinned_per_request(tmp_path: Path):
    real = tmp_path / "real.json"
    _real_release(real)
    calls = []
    from zion_terminal.truth_query import RealReleaseClient
    client_impl = RealReleaseClient(real)
    original = client_impl._snapshot
    def snapshot_once():
        calls.append(1)
        return original()
    client_impl._snapshot = snapshot_once
    app = create_app(registry=__import__("zion_terminal.world_router", fromlist=["WorldRegistry"]).WorldRegistry())
    app.state.test_registry = client_impl
    # Direct client assertion: one handler invocation loads one immutable snapshot.
    request = {"query": "AAPL revenue", "world": {"world_type": "real", "world_id": "us-public-markets"}, "request_id": "r"}
    client_impl.handle(request)
    assert len(calls) == 1
