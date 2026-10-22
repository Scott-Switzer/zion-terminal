import json
from types import SimpleNamespace

import pytest

from zion_terminal.history_store import acquire, build_release


def rows(raw, task):
    return [
        dict(
            series_id="test:rate",
            observation_date="2000-01-03",
            value_decimal=raw.decode(),
            missing_reason=None,
            unit="percent",
            asset_class="rates",
            label="Observed test rate",
        )
    ]


def test_verified_resume_skips_network_and_corruption_requires_refetch(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return dict(raw=b"2.5", etag="v1", last_modified=None)

    tasks = [dict(id="rate:2000", dataset="test", url="https://home.treasury.gov/test.csv")]
    adapters = {"test": SimpleNamespace(parse=rows)}
    first = acquire(tasks, adapters, tmp_path, fetcher=fetch)
    second = acquire(tasks, adapters, tmp_path, fetcher=fetch)
    assert len(calls) == 1 and second["cached_tasks"] == 1
    assert first["observations"] == second["observations"]
    raw = next((tmp_path / "raw").iterdir())
    raw.write_bytes(b"0")
    third = acquire(tasks, adapters, tmp_path, fetcher=fetch)
    assert len(calls) == 2 and third["fetched_tasks"] == 1


def test_partial_failure_checkpoint_survives_and_does_not_hide_gaps(tmp_path):
    tasks = [
        dict(id="ok", dataset="test", url="https://home.treasury.gov/ok"),
        dict(id="fail", dataset="test", url="https://home.treasury.gov/fail"),
    ]

    def fetch(url):
        if url.endswith("fail"):
            raise TimeoutError("secret must not be logged")
        return dict(raw=b"2.5", etag=None, last_modified=None)

    result = acquire(tasks, {"test": SimpleNamespace(parse=rows)}, tmp_path, fetcher=fetch)
    assert len(result["failures"]) == 1
    assert result["failures"][0]["error"] == "TimeoutError"
    assert len(list((tmp_path / "checkpoints").iterdir())) == 1
    assert len(result["observations"]) == 1


def test_release_preserves_nulls_units_raw_lineage_and_is_deterministic(tmp_path):
    observed = rows(b"-0.25", {})
    observed[0]["source_id"] = "task"
    missing = dict(
        observed[0],
        observation_date="2000-01-04",
        value_decimal=None,
        missing_reason="SOURCE_MISSING",
    )
    sources = {
        "task": dict(
            url="https://home.treasury.gov/test.csv",
            source_sha256="a" * 64,
            retrieved_at="2026-10-19T00:00:00Z",
        )
    }
    a = build_release(observed + [missing], sources, tmp_path)
    b = build_release(list(reversed(observed + [missing])), sources, tmp_path)
    assert a == b
    manifest = json.loads((tmp_path / a["relative_manifest"]).read_bytes())
    item = manifest["series"]["test:rate"]
    assert item["observations"] == 1 and item["missing"] == 1
    assert manifest["point_in_time_status"] == "NOT_ESTABLISHED"
    assert manifest["sources"]["task"]["source_sha256"] == "a" * 64
    series = json.loads((tmp_path / item["local_path"]).read_bytes())
    assert series["rows"][1][1:3] == [None, "SOURCE_MISSING"]
    with pytest.raises(ValueError, match="conflict"):
        build_release(observed + [dict(observed[0], value_decimal="9")], sources, tmp_path)


def test_invalid_value_cannot_become_a_release(tmp_path):
    item = rows(b"NaN", {})[0]
    item["source_id"] = "task"
    with pytest.raises(ValueError):
        build_release([item], {}, tmp_path)


def test_discovery_changes_content_address(tmp_path):
    row = rows(b"2.5", {})[0]
    row["source_id"] = "task"
    sources = {"task": dict(source_sha256="a" * 64)}
    first = build_release([row], sources, tmp_path)
    second = build_release([row], sources, tmp_path, discovery=[{"id": "new-source"}])
    assert first["release_id"] != second["release_id"]
