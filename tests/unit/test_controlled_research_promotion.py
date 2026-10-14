import importlib.util
from pathlib import Path

p = Path(__file__).parents[2] / "scripts/controlled_research_promotion.py"
spec = importlib.util.spec_from_file_location("promotion", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_validation_failure_rolls_back_and_verifies_before_reporting():
    events = []

    def verify():
        raise RuntimeError("bad candidate")

    report = m.run_controlled(
        preflight=lambda: events.append("preflight"),
        activate=lambda: events.append("activate") or "new",
        verify=verify,
        rollback=lambda: events.append("rollback"),
        verify_previous=lambda: events.append("old_verified"),
        record=lambda r: None,
    )
    assert report["status"] == "ROLLED_BACK"
    assert events == ["preflight", "activate", "rollback", "old_verified"]


def test_preflight_denial_never_deploys_or_rolls_back():
    events = []

    def preflight():
        raise RuntimeError("denied")

    report = m.run_controlled(
        preflight=preflight,
        activate=lambda: events.append("activate"),
        verify=lambda: None,
        rollback=lambda: events.append("rollback"),
        verify_previous=lambda: None,
        record=lambda r: None,
    )
    assert report["status"] == "PREFLIGHT_FAILED" and not events


def test_partial_activation_failure_also_restores_previous_version():
    events = []

    def activate():
        events.append("partial")
        raise RuntimeError("transport after activation")

    report = m.run_controlled(
        preflight=lambda: None,
        activate=activate,
        verify=lambda: None,
        rollback=lambda: events.append("rollback"),
        verify_previous=lambda: events.append("old_verified"),
        record=lambda r: None,
    )
    assert report["status"] == "ROLLED_BACK" and events == ["partial", "rollback", "old_verified"]


def test_failed_rollback_is_never_reported_as_restored():
    def fail():
        raise RuntimeError("failure")

    report = m.run_controlled(
        preflight=lambda: None,
        activate=lambda: "new",
        verify=fail,
        rollback=fail,
        verify_previous=lambda: None,
        record=lambda r: None,
    )
    assert report["status"] == "ROLLBACK_UNVERIFIED" and report["rollback_verified"] is False


def test_success_activates_only_after_preflight_and_has_no_rollback():
    events = []
    report = m.run_controlled(
        preflight=lambda: events.append("preflight"),
        activate=lambda: "new",
        verify=lambda: events.append("verified"),
        rollback=lambda: events.append("rollback"),
        verify_previous=lambda: None,
        record=lambda r: None,
    )
    assert report["status"] == "PASS" and report["candidate_version_id"] == "new"
    assert events == ["preflight", "verified"]


def test_active_version_uses_timestamp_instead_of_array_order():
    old = "11111111-1111-1111-1111-111111111111"
    new = "22222222-2222-2222-2222-222222222222"
    rows = [
        {"created_on": "2026-10-14", "versions": [{"version_id": new, "percentage": 100}]},
        {"created_on": "2026-10-13", "versions": [{"version_id": old, "percentage": 100}]},
    ]
    assert m.single_active_version(rows) == new


def test_active_version_rejects_split_traffic():
    import pytest
    with pytest.raises(RuntimeError, match="SPLIT"):
        m.single_active_version([{"created_on": "2026-10-14", "versions": [
            {"version_id": "11111111-1111-1111-1111-111111111111", "percentage": 50},
            {"version_id": "22222222-2222-2222-2222-222222222222", "percentage": 50},
        ]}])
