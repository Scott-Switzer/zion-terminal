import sys
from pathlib import Path

import pytest

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
    with pytest.raises(ValueError, match="SOURCE_REFRESH_ONLY_GATE"):
        composer.publish_update(s, parentref, original, base, candidate, tmp_path / "denied")
    assert len(s.writes) == before
