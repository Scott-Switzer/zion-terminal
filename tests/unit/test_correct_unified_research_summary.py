import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "correct_unified", Path(__file__).parents[2] / "scripts/correct_unified_research_summary.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def companion(cik):
    return dict(
        cik=cik,
        rights="PUBLIC_SEC_ONLY",
        licensed_sources_allowed=False,
        certification={
            "observations": [{"status": "ACCOUNTING_CERTIFIED"}],
            "coverage_gaps": [{"status": "UNAVAILABLE"}] * 2,
            "summary": {"ACCOUNTING_CERTIFIED": 1, "UNAVAILABLE": 2},
        },
        reconciliation_evidence={"checks": [{"status": "PASS"}]},
    )


def test_observations_and_gaps_are_counted_separately_for_unique_ciks():
    docs = {"0000000001": companion("0000000001"), "0000000002": companion("0000000002")}
    old = {
        "companies": [
            {
                "cik": k,
                "bytes": 123,
                "sha256": "hash",
                "status_counts": {"ACCOUNTING_CERTIFIED": 1, "UNAVAILABLE": 2},
            }
            for k in docs
        ],
        "union_current_series_observations": 6,
        "union_accounting_certified_observations": 2,
    }
    updated = m.correct_summary(old, docs)
    assert updated["observation_count"] == 2 and updated["coverage_gap_count"] == 4
    assert updated["observation_status_counts"] == {"ACCOUNTING_CERTIFIED": 2}
    assert updated["companies"] == old["companies"]
    assert updated["aggregate_correction"]["cause"] == "MIXED_OBSERVATION_AND_GAP_COUNTS"
    assert old["union_current_series_observations"] == 6


def test_duplicate_cik_metadata_fails_without_double_count():
    old = {
        "companies": [{"cik": "0000000001"}, {"cik": "0000000001"}],
        "union_current_series_observations": 2,
        "union_accounting_certified_observations": 2,
    }
    with pytest.raises(ValueError):
        m.correct_summary(old, {"0000000001": companion("0000000001")})


def test_successor_reuses_all_source_refs_and_binds_predecessor_and_new_summary():
    old = dict(
        schema_version="unified-research-release-v1",
        release_id="old",
        certifications={"1": {"key": "old/cert", "sha256": "a", "bytes": 1}},
        financial_entries={"1": {"exact": "entry"}},
        market={"key": "old/market", "sha256": "b"},
        identity={"key": "old/identity", "sha256": "c"},
        source_policy={"public_sec": True},
        content_identity={"old": "identity"},
        certification_summary={"key": "old/summary", "sha256": "d"},
    )
    previous = {"key": "old/manifest", "sha256": "e", "bytes": 100}
    summary = {"key": "new/summary", "sha256": "f", "bytes": 200}
    release_id = m.hashlib.sha256(m.encode(m.successor_identity(previous, summary))).hexdigest()
    new = m.successor_root(old, previous, summary, release_id)
    for key in ["certifications", "financial_entries", "market", "identity", "source_policy"]:
        assert new[key] == old[key]
    assert new["predecessor_root"] == previous
    assert new["certification_summary"] == summary and new["release_id"] == release_id
    assert new["content_identity"]["predecessor_root"] == previous
    assert new["content_identity"]["corrected_summary"]["sha256"] == summary["sha256"]
    assert old["certification_summary"]["key"] == "old/summary"
