from zion_terminal.world_router import WorldRegistry, WorldRouteError


def response(world_type: str, world_id: str) -> dict:
    return {
        "world": {"world_type": world_type, "world_id": world_id, "version": "native"},
        "entity": {"entity_id": "x", "display_name": "X", "symbol": "X"},
        "answer": "revenue=1 USD",
        "metrics": [{"metric": "revenue", "value": 1, "unit": "USD", "period": "FY2025"}],
        "calculations": [], "evidence": [], "quality": {"status": "PASS"},
    }


def test_real_and_synthetic_route_to_same_contract():
    registry = WorldRegistry()
    registry.register("real", lambda request: response("real", request["world"]["world_id"]))
    registry.register("synthetic", lambda request: response("synthetic", request["world"]["world_id"]))
    real = registry.query({"query": "AAPL revenue", "world": {"world_type": "real", "world_id": "us-public-markets", "version": "native"}})
    synthetic = registry.query({"query": "NOVA revenue", "world": {"world_type": "synthetic", "world_id": "test-world-001", "version": "native"}})
    assert set(real) == set(synthetic)
    assert real["world"]["world_type"] == "real"
    assert synthetic["world"]["world_type"] == "synthetic"


def test_router_rejects_mismatched_response_world():
    registry = WorldRegistry()
    registry.register("real", lambda request: response("synthetic", request["world"]["world_id"]))
    try:
        registry.query({"query": "AAPL", "world": {"world_type": "real", "world_id": "us-public-markets", "version": "native"}})
    except WorldRouteError as exc:
        assert "world_type" in str(exc)
    else:
        raise AssertionError("mismatched world response was accepted")
