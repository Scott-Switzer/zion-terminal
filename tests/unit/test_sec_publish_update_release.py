import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from sec_publish_update_release import Budget, chunks, ordered_rows


def test_budget_denies_before_create():
    budget = Budget(maximum_bytes=4, maximum_creates=1)
    budget.reserve(4)
    with pytest.raises(ValueError, match="PUBLICATION_BUDGET"):
        budget.reserve(1)


def test_metric_sort_and_bounded_chunks():
    rows = [
        {"cik": "b", "metrics": {"x": {"value_decimal": "10"}}},
        {"cik": "a", "metrics": {"x": {"value_decimal": "2"}}},
    ]
    assert [r["cik"] for r in ordered_rows(rows, "x")] == ["a", "b"]
    assert len(list(chunks(rows, 150))) == 2


def test_cloud_composer_preserves_cutoff_and_inheritance(tmp_path, monkeypatch):
    import io
    import json

    import sec_publish_update_release as composer
    from materialize_sec_engine import REGISTRY, digest, encoded, materialize
    from sec_incremental_update import build_candidate
    from test_sec_history import fact, source

    class Store:
        def __init__(self):
            self.objects = {}
            self.writes = []

        def get_object(self, **kw):
            return {"Body": io.BytesIO(self.objects[kw["Key"]])}

        def put_object(self, **kw):
            assert kw["IfNoneMatch"] == "*"
            assert kw["Key"] not in self.objects
            self.objects[kw["Key"]] = kw["Body"]
            self.writes.append(kw["Key"])

        def ref(self, key, body):
            self.objects[key] = body
            return dict(key=key, sha256=digest(body), bytes=len(body))

    s = Store()
    base = tmp_path / "base"
    base.mkdir()
    candidate = tmp_path / "candidate"
    core = Path(__file__).parents[2] / "deploy/cloudflare-query/src"
    registry = encoded(
        dict(metrics=REGISTRY, implementation_sha256=digest((core / "sec_engine.py").read_bytes()))
    )
    cik = "0000320193"
    raw = encoded(source([fact()]))
    run = "a" * 64
    data = materialize(raw, cik, {}, {}, run, digest(registry))
    prior = dict(
        release_id=run,
        registry_sha256=digest(registry),
        rights="PUBLIC_SEC_ONLY",
        licensed_sources_allowed=False,
        source={},
        companies={},
    )
    (base / "engine-manifest.json").write_bytes(encoded(prior))
    (base / "registry.json").write_bytes(registry)
    _, objects = build_candidate(
        prior, encoded(prior), {cik: (raw, {}, None)}, "2025-01-01T00:00:00Z"
    )
    for name, body in objects.items():
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    companyref = s.ref("old/company", encoded(data))
    companyref["source_release_id"] = run
    shard = s.ref("old/shard", encoded({cik: dict(materialized=companyref, policy=data["policy"])}))
    row = dict(cik=cik, label="x", metrics={}, missing={})
    screen = s.ref(
        "old/screen",
        encoded(dict(companies=[row], release_id="b" * 64, registry_sha256=digest(registry))),
    )
    parent = dict(
        prior,
        release_id="b" * 64,
        date_from="1900-01-01",
        date_to="2024-01-01",
        maximum_as_of="2024-01-01T00:00:00Z",
        source_releases=[run],
        registry=s.ref("old/registry", registry),
        shards={"93": shard, "00": dict(key="untouched", sha256="c" * 64, bytes=2)},
        screening_metrics=dict(metrics={"revenue": [screen]}),
    )
    parentref = s.ref("old/root", encoded(parent))
    original = s.ref("original/root", encoded(dict(parent, release_id="d" * 64)))
    monkeypatch.setattr(composer, "independent_check", lambda *a: dict(status="PASS"))
    receipt = composer.publish_update(s, parentref, original, base, candidate, tmp_path / "output")
    new = json.loads(s.objects[receipt["root"]["key"]])
    assert new["maximum_as_of"] == parent["maximum_as_of"]
    assert new["date_to"] == parent["date_to"]
    assert new["balance_quality_version"] == "sec-balance-same-vintage-v1"
    assert "balance_quality_version" not in parent
    assert new["balance_quality_implementation"]["key"].endswith(
        "/implementation/sec_balance_quality.py"
    )
    assert new["shards"]["00"] == parent["shards"]["00"]
    assert set(new["source_releases"]) == {run, new["release_id"]}
    assert new["previous_releases"][parent["release_id"]] == parentref
    assert new["update_refresh"]["financial_observation_changes"] is False
    assert not any("CURRENT" in key for key in s.writes)
    assert s.writes[-1].endswith("/manifest.json")
    assert receipt["current_pointer_writes"] == 0

    original_overlay = composer.candidate_overlay

    def changed_overlay(*args, **kwargs):
        updated, report = original_overlay(*args, **kwargs)
        report["reconciliation"]["added_source_ids"] = ["synthetic-denial-test-only"]
        return updated, report

    monkeypatch.setattr(composer, "candidate_overlay", changed_overlay)
    before = len(s.writes)
    with pytest.raises(ValueError, match="ACCOUNTING_PASS_REQUIRED"):
        composer.publish_update(s, parentref, original, base, candidate, tmp_path / "denied")
    assert len(s.writes) == before


def test_post_cutoff_financial_addition_preserves_historical_results():
    from materialize_sec_engine import encoded, materialize
    from sec_publish_update_release import update_histories
    from test_sec_history import fact, source

    old_raw = encoded(source([fact()]))
    new_raw = encoded(
        source(
            [
                fact(),
                fact(start="2023-01-01", end="2023-12-31", filed="2024-02-01", accn="b", val=250),
            ]
        )
    )
    old = materialize(old_raw, "0000320193", {}, {}, "a" * 64, "b" * 64)
    new = materialize(new_raw, "0000320193", {}, {}, "c" * 64, "b" * 64)
    root = dict(date_to="2024-01-01", maximum_as_of="2024-01-01T00:00:00Z")
    fresh, fixed, gate = update_histories(old, new, root, "2025-01-01T00:00:00Z")
    assert gate["status"] == "PASS"
    assert len(fresh["annual"]["observations"]) > len(fixed["annual"]["observations"])
    new["observations"][0]["value_decimal"] = "999"
    with pytest.raises(ValueError, match="HISTORICAL_AVAILABILITY_REPRODUCTION_FAILED"):
        update_histories(old, new, root, "2025-01-01T00:00:00Z")


def test_history_gap_reproduction_failure_blocks(monkeypatch):
    import sec_publish_update_release as composer

    calls = iter(
        [
            {"observations": [], "coverage_gaps": []},
            {"observations": [], "coverage_gaps": [{"reason": "changed"}]},
        ]
    )
    monkeypatch.setattr(composer, "history", lambda *a, **kw: next(calls))
    with pytest.raises(ValueError, match="HISTORICAL_AVAILABILITY_REPRODUCTION_FAILED"):
        composer.update_histories(
            {"observations": []},
            {"observations": []},
            dict(date_to="2024-01-01", maximum_as_of="2024-01-01T00:00:00Z"),
            "2025-01-01T00:00:00Z",
        )


def test_raw_metadata_id_changes_preserve_financial_projection():
    from materialize_sec_engine import encoded, materialize
    from sec_publish_update_release import update_histories
    from test_sec_history import fact, source

    old = materialize(encoded(source([fact()])), "0000320193", {}, {}, "a" * 64, "b" * 64)
    changed = fact()
    changed["frame"] = "CY2020"
    changed["fy"] = 2021
    new = materialize(encoded(source([changed])), "0000320193", {}, {}, "c" * 64, "b" * 64)
    old_id = old["observations"][0]["source_observation_id"]
    assert old_id != new["observations"][0]["source_observation_id"]
    _, _, gate = update_histories(
        old,
        new,
        dict(date_to="2024-01-01", maximum_as_of="2024-01-01T00:00:00Z"),
        "2025-01-01T00:00:00Z",
    )
    assert gate["status"] == "PASS"
    assert old["observations"][0]["source_observation_id"] == old_id


def test_same_net_value_with_changed_component_blocks():
    from sec_publish_update_release import history_signature

    def source(sid, value):
        return dict(
            source_observation_id=sid,
            concept="us-gaap:Cash",
            unit="USD",
            currency="USD",
            value_decimal=value,
            period_start=None,
            period_end="2020-12-31",
            available_at="2021-02-01T00:00:00Z",
            accession="a",
            form="10-K",
            source_record={"filed": "2021-01-31"},
        )

    def result(a, b):
        return dict(
            observations=[
                dict(
                    value_decimal="5",
                    source_terms=[
                        dict(source_observation_id=a, coefficient=1),
                        dict(source_observation_id=b, coefficient=-1),
                    ],
                )
            ],
            coverage_gaps=[],
        )

    assert history_signature(
        result("a", "b"), {"observations": [source("a", "10"), source("b", "5")]}
    ) != history_signature(
        result("c", "d"), {"observations": [source("c", "11"), source("d", "6")]}
    )


def test_pre_cutoff_availability_change_blocks():
    from materialize_sec_engine import encoded, materialize
    from sec_publish_update_release import update_histories
    from test_sec_history import fact, source

    old = materialize(encoded(source([fact()])), "0000320193", {}, {}, "a" * 64, "b" * 64)
    changed = fact(filed="2021-03-01")
    new = materialize(encoded(source([changed])), "0000320193", {}, {}, "c" * 64, "b" * 64)
    with pytest.raises(ValueError, match="HISTORICAL_AVAILABILITY_REPRODUCTION_FAILED"):
        update_histories(
            old,
            new,
            dict(date_to="2024-01-01", maximum_as_of="2024-01-01T00:00:00Z"),
            "2025-01-01T00:00:00Z",
        )
