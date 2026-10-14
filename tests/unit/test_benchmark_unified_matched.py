import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "benchmark_unified_matched", Path(__file__).parents[2] / "scripts/benchmark_unified_matched.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_exact_projection_preserves_duplicates_decimal_tokens_and_lineage():
    row = {
        "metric": "revenue",
        "value_decimal": "9007199254740993.10",
        "source_observation_ids": ["x"],
    }
    original = {
        "observations": [row, row],
        "coverage_gaps": [],
        "source_records": {"x": {"unit": "USD"}},
        "cache": {"state": "MISS"},
    }
    equivalent = {**original, "cache": {"state": "HIT"}}
    assert m.digest(m.projection(original)) == m.digest(m.projection(equivalent))
    assert m.digest(m.projection(original)) != m.digest(
        m.projection({**original, "observations": [row]})
    )
    changed = {**original, "source_records": {"x": {"unit": "EUR"}}}
    assert m.digest(m.projection(original)) != m.digest(m.projection(changed))


def test_nearest_rank_percentiles_and_measured_bytes():
    rows = [{"latency_ms": i, "response_bytes": 10, "cache_state": "HIT"} for i in range(1, 21)]
    result = m.summarize(rows)
    assert result["p50_ms"] == 10
    assert result["p95_ms"] == 19
    assert result["response_bytes"] == 200
    assert result["cache_states"] == {"HIT": 20}
    assert m.percentile([], 0.95) is None
