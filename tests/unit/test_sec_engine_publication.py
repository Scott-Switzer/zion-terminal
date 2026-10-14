import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from materialize_sec_engine import release_identity


def test_expanded_materialized_cohort_cannot_overwrite_previous_release_identity():
    source = {"etag": "owned", "snapshot_date": "2026-09-24"}
    assert release_identity(
        source, "a" * 64, {"0000789019": {}}, ["0000789019"]
    ) != release_identity(source, "a" * 64, {"0000789019": {}}, ["0000789019", "0000320193"])


def test_bad_archive_identity_is_quarantined_without_remapping_or_aborting_cohort():
    import json

    from materialize_sec_engine import evaluate_member

    raw = json.dumps({"cik": 123, "facts": {}}).encode()
    data, error = evaluate_member(raw, "0000789019", {}, {}, "b" * 64, "c" * 64)
    assert data is None and error == "SOURCE_IDENTITY_MISMATCH"
