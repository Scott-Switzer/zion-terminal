import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).parents[2] / "scripts/sec_universe_pipeline.py"
spec = importlib.util.spec_from_file_location("universe", path)
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def test_stage_progression_has_no_pilot_ceiling():
    assert u.expansion_stages(20396) == [2000, 5000, 20396]
    assert u.expansion_stages(1500) == [1500]


def test_budget_stops_before_unauthorized_write():
    ledger = u.Budget(5, maximum_bytes=100)
    ledger.reserve_write(90)
    with pytest.raises(u.ResourceStop, match="STORAGE"):
        ledger.reserve_write(11)
    assert ledger.bytes == 90


def test_checkpoint_context_cannot_be_reused_silently(tmp_path):
    p = tmp_path / "one.json.gz"
    u.save_checkpoint(p, {"context": "a", "status": "QUARANTINED"})
    assert u.load_checkpoint(p, "a")["status"] == "QUARANTINED"
    with pytest.raises(ValueError, match="CHECKPOINT_CONTEXT"):
        u.load_checkpoint(p, "b")


def test_partial_publication_identity_changes_when_checkpoint_set_expands():
    records = {"0000000001": {"status": "QUARANTINED", "reason": "NO_CONTEXT"}}
    first = u.publication_identity("a" * 64, records)
    records["0000000002"] = {"status": "USABLE", "ref": {"sha256": "b" * 64}}
    assert u.publication_identity("a" * 64, records) != first
