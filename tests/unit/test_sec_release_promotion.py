import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from sec_release_promotion import promotion_gate, reconcile


def test_incomplete_and_failed_gates_deny():
    with pytest.raises(ValueError, match="PROMOTION_GATE"):
        promotion_gate({"coverage": True})
    with pytest.raises(ValueError, match="PROMOTION_GATE"):
        promotion_gate(
            dict(
                coverage=True,
                accounting=True,
                anomalies=False,
                reconciliation=True,
                provenance=True,
            )
        )


def test_revisions_and_deletions_are_explicit():
    def row(sid, value):
        return dict(
            source_observation_id=sid,
            concept="us-gaap:Revenues",
            unit="USD",
            period_start="2020-01-01",
            period_end="2020-12-31",
            accession="a",
            value_decimal=value,
        )

    result = reconcile([row("old", "1"), row("gone", "3")], [row("new", "2")])
    assert result["removed_source_ids"] == ["gone", "old"]
    assert result["added_source_ids"] == ["new"]
    assert result["revised_contexts"][0]["old_values"] == ["1", "3"]


def test_compose_rebuilds_registry_and_preserves_base(tmp_path):
    import json

    from materialize_sec_engine import REGISTRY, VERSION, digest, encoded, materialize
    from sec_incremental_update import build_candidate
    from sec_release_promotion import candidate_overlay, compose_candidate
    from test_sec_history import fact, source

    core = Path(__file__).parents[2] / "deploy/cloudflare-query/src"
    base, candidate, output = (tmp_path / name for name in ("base", "candidate", "new"))
    base.mkdir()
    registry = dict(
        version=VERSION,
        metrics=REGISTRY,
        implementation_sha256=digest((core / "sec_engine.py").read_bytes()),
    )
    rb = encoded(registry)
    old_raw = encoded(source([fact()]))
    cik = "0000320193"
    old = materialize(old_raw, cik, {}, {}, "a" * 64, digest(rb))
    body = encoded(old)
    previous = dict(
        release_id="a" * 64,
        registry_sha256=digest(rb),
        rights="PUBLIC_SEC_ONLY",
        licensed_sources_allowed=False,
        source={},
        date_from="1900-01-01",
        date_to="2025-01-01",
        maximum_as_of="2025-01-01T00:00:00Z",
        companies={cik: dict(sha256=digest(body))},
    )
    for name, value in [
        ("engine-manifest.json", encoded(previous)),
        ("registry.json", rb),
        ("full-member-index.json", b"{}"),
        (cik + ".history.json", body),
    ]:
        (base / name).write_bytes(value)
    _, objects = build_candidate(
        previous,
        encoded(previous),
        {cik: (encoded(source([fact(val=200)])), {}, old_raw)},
        "2025-01-01T00:00:00Z",
    )
    for name, value in objects.items():
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    overlay, report = candidate_overlay(candidate, cik, old, "c" * 64, digest(rb))
    assert overlay["release_id"] == "c" * 64
    assert report["checks"]["accounting"]
    assert overlay["observations"][0]["value_decimal"] == "200"
    receipt = compose_candidate(base, candidate, output)
    assert receipt["status"] == "PASS"
    assert (base / (cik + ".history.json")).read_bytes() == body
    updated = json.loads((output / (cik + ".history.json")).read_bytes())
    assert updated["release_id"] == receipt["release_id"]
    assert updated["observations"][0]["value_decimal"] == "200"
    assert receipt["checks"][cik]["reconciliation"]["removed_source_ids"]
    # Hash corruption cannot create a release manifest.
    raw_path = next((candidate / "raw" / cik).glob("*.json"))
    raw_path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="CANDIDATE_HASH_MISMATCH"):
        compose_candidate(base, candidate, tmp_path / "denied")
    assert not (tmp_path / "denied" / "engine-manifest.json").exists()
