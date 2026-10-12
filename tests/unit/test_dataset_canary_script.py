"""Behaviour of the operator-run canary, including its refusal paths.

The script is the only component that can overwrite the approved baseline, so its
safety properties are tested directly: a FAIL comparison never advances the
baseline, a WARNING needs an explicit override, an incomparable pair is refused
cleanly, and every accepted baseline lands in the audit history. All tests run
offline against the synthetic fixture tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_TREE = ROOT / "tests" / "fixtures" / "datasets" / "tree"
FIXTURE_BASELINE = ROOT / "tests" / "fixtures" / "datasets" / "baselines" / "fixture-report.json"


def run_canary(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dataset_canary.py"), *args],
        cwd=cwd or ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_digest(report: dict) -> dict:
    """Keep a mutated report's content address consistent with its content."""
    body = {
        key: value for key, value in report.items() if key not in {"generated_at", "report_sha256"}
    }
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    report["report_sha256"] = hashlib.sha256(payload).hexdigest()
    return report


def mutate_value(report: dict, ticker: str, period: str, field_id: str, value: str) -> dict:
    """Change a value while leaving accession, filing and source object intact."""
    for company in report["companies"]:
        if company["ticker"] != ticker:
            continue
        entry = company["contract"]["fields"][period][field_id]
        entry["value"] = value
        entry["observation"]["value"] = value
    return report


@pytest.fixture()
def workspace(tmp_path):
    baseline = tmp_path / "approved-baseline.json"
    baseline.write_text(FIXTURE_BASELINE.read_text())
    return {"baseline": baseline, "out": tmp_path / "out"}


def base_args(workspace) -> list[str]:
    return [
        "--fixture",
        str(FIXTURE_TREE),
        "--tickers",
        "TSTA,TSTB",
        "--as-of",
        "2026-10-11",
        "--baseline",
        str(workspace["baseline"]),
        "--out",
        str(workspace["out"]),
    ]


def test_comparison_against_the_approved_baseline_passes(workspace):
    result = run_canary(*base_args(workspace))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["overall_severity"] == "PASS"
    assert payload["baseline_advanced"] is False
    assert Path(payload["written"]["summary"]).is_file()


def test_a_failing_comparison_never_advances_the_baseline(workspace):
    baseline_report = json.loads(FIXTURE_BASELINE.read_text())
    mutate_value(baseline_report, "TSTA", "2025-12-31", "revenue", "123456789012")
    workspace["baseline"].write_text(json.dumps(refresh_digest(baseline_report)))
    before = digest(workspace["baseline"])

    result = run_canary(
        *base_args(workspace), "--accept-baseline", "--reason", "must be refused", "--actor", "test"
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "refusing to advance the baseline" in result.stderr
    assert digest(workspace["baseline"]) == before
    assert not (workspace["out"] / "baseline_history.jsonl").exists()


def test_a_warning_baseline_needs_an_explicit_override(workspace):
    """A baseline that lost a fiscal period is a WARNING, not a silent pass."""
    baseline_report = json.loads(FIXTURE_BASELINE.read_text())
    for company in baseline_report["companies"]:
        fields = company["contract"]["fields"]
        fields["2022-12-31"] = json.loads(json.dumps(fields["2023-12-31"]))
        company["contract"]["periods"] = ["2022-12-31", *company["contract"]["periods"]]
    workspace["baseline"].write_text(json.dumps(refresh_digest(baseline_report)))
    before = digest(workspace["baseline"])

    refused = run_canary(
        *base_args(workspace), "--accept-baseline", "--reason", "needs the override"
    )
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "without --allow-warning-baseline" in refused.stderr
    assert digest(workspace["baseline"]) == before

    accepted = run_canary(
        *base_args(workspace),
        "--accept-baseline",
        "--reason",
        "reviewed the pre-existing condition",
        "--actor",
        "test",
        "--allow-warning-baseline",
    )
    assert accepted.returncode == 1, accepted.stdout + accepted.stderr
    assert json.loads(accepted.stdout)["baseline_advanced"] is True
    assert digest(workspace["baseline"]) != before
    history = (workspace["out"] / "baseline_history.jsonl").read_text().strip().splitlines()
    assert len(history) == 1
    record = json.loads(history[0])
    assert record["actor"] == "test"
    assert record["reason"] == "reviewed the pre-existing condition"
    assert record["severity"] == "WARNING"
    assert record["bootstrap"] is False
    assert record["previous_baseline_sha256"] and record["baseline_sha256"]
    assert record["baseline_sha256"] != record["previous_baseline_sha256"]


def test_bootstrap_records_an_audited_first_baseline(tmp_path):
    out = tmp_path / "out"
    baseline = tmp_path / "approved-baseline.json"
    result = run_canary(
        "--fixture",
        str(FIXTURE_TREE),
        "--tickers",
        "TSTA,TSTB",
        "--as-of",
        "2026-10-11",
        "--baseline",
        str(baseline),
        "--out",
        str(out),
        "--accept-baseline",
        "--reason",
        "first approved baseline",
        "--actor",
        "test",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["overall_severity"] is None
    assert payload["baseline_advanced"] is True
    assert baseline.is_file()
    record = json.loads((out / "baseline_history.jsonl").read_text().strip())
    assert record["bootstrap"] is True
    assert record["comparison_sha256"] is None
    assert record["reason"] == "first approved baseline"


def test_first_run_without_acceptance_asks_for_one(tmp_path):
    """With neither a private nor a committed baseline, the run asks to bootstrap."""
    result = run_canary(
        "--fixture",
        str(FIXTURE_TREE),
        "--tickers",
        "TSTA,TSTB",
        "--as-of",
        "2026-10-11",
        "--baseline",
        str(tmp_path / "missing.json"),
        "--out",
        str(tmp_path / "out"),
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert "--accept-baseline --reason" in result.stderr
    assert "Traceback" not in result.stderr


def test_acceptance_requires_a_reason(workspace):
    result = run_canary(*base_args(workspace), "--accept-baseline")
    assert result.returncode == 2
    assert "--reason" in result.stderr


def test_incomparable_inputs_are_refused_cleanly(workspace):
    """A live baseline against a fixture candidate must not crash or advance."""
    live = json.loads(FIXTURE_BASELINE.read_text())
    live["mode"] = "live"
    workspace["baseline"].write_text(json.dumps(live))
    before = digest(workspace["baseline"])

    result = run_canary(*base_args(workspace), "--accept-baseline", "--reason", "must be refused")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "INCOMPARABLE_REPORTS" in result.stderr
    assert "Traceback" not in result.stderr
    assert digest(workspace["baseline"]) == before
    assert not (workspace["out"] / "baseline_history.jsonl").exists()
