"""Exercise the documented ``compare`` CLI, not only the comparison library.

These tests drive ``python -m zion_terminal.datasets compare`` as an operator or a
scheduled job would: exit status, stdout shape, artifacts written, the expected
severity gate, and the offline canary path that rebuilds the synthetic fixture
report and compares it against the committed approved baseline.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "tests" / "fixtures" / "datasets" / "comparisons"
BASELINE = ROOT / "tests" / "fixtures" / "datasets" / "baselines" / "fixture-report.json"
FIXTURE_TREE = ROOT / "tests" / "fixtures" / "datasets" / "tree"

#: Private provenance markers. Key *names* such as ``comparison_sha256`` are
#: deliberately not listed: the sanitized summary publishes the comparison's own
#: content address, and the leakage guard allows exactly that digest.
PRIVATE_MARKERS = (
    "sec/bulk/",
    "sec-filing-intelligence/",
    "financial-data-wrds-private",
    "financial-system-datasets",
    "ppe-sec-intelligence-prod",
    "companyfacts.json",
    "fixture-store-a",
)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "zion_terminal.datasets", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def scenario_args(name: str) -> list[str]:
    return [
        "compare",
        "--baseline",
        str(SCENARIOS / name / "baseline.json"),
        "--candidate",
        str(SCENARIOS / name / "candidate.json"),
    ]


def test_documented_compare_command_writes_every_artifact(tmp_path):
    result = run_cli(*scenario_args("unexplained_value_change"), "--out", str(tmp_path))
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert payload["overall_severity"] == "FAIL"
    assert payload["exit_status"] == 2
    assert payload["expected_severity"] is None
    assert payload["status"] == "no_expectation"
    assert payload["companies"]["TSTA"]["severity"] == "FAIL"
    for key in ("comparison", "comparison_markdown", "summary", "summary_markdown"):
        assert Path(payload["written"][key]).is_file(), key
    summary = Path(payload["written"]["summary"]).read_text()
    assert "VALUE_CHANGED_WITHOUT_SOURCE_REVISION" in summary
    for marker in PRIVATE_MARKERS:
        assert marker not in summary, marker


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [("unchanged", 0), ("new_period", 0), ("field_lost", 1), ("invalid_unit_conversion", 2)],
)
def test_exit_status_follows_the_documented_severity_scale(scenario, expected_status, tmp_path):
    result = run_cli(*scenario_args(scenario), "--out", str(tmp_path))
    assert result.returncode == expected_status, result.stderr
    assert json.loads(result.stdout)["exit_status"] == expected_status


def test_expected_severity_gate_is_strict(tmp_path):
    mismatch = run_cli(
        *scenario_args("unexplained_value_change"),
        "--out",
        str(tmp_path),
        "--expect-severity",
        "PASS",
    )
    assert mismatch.returncode == 2
    assert "severity mismatch" in mismatch.stderr
    assert json.loads(mismatch.stdout)["status"] == "unexpected"

    matching = run_cli(
        *scenario_args("unexplained_value_change"),
        "--out",
        str(tmp_path),
        "--expect-severity",
        "FAIL",
    )
    assert matching.returncode == 0, matching.stderr
    assert json.loads(matching.stdout)["status"] == "as_expected"

    healthy = run_cli(
        *scenario_args("unchanged"), "--out", str(tmp_path), "--expect-severity", "PASS"
    )
    assert healthy.returncode == 0, healthy.stderr


def test_summary_only_writes_no_private_comparison(tmp_path):
    result = run_cli(*scenario_args("field_lost"), "--out", str(tmp_path), "--summary-only")
    assert result.returncode == 1
    written = json.loads(result.stdout)["written"]
    assert "comparison" not in written
    assert not (tmp_path / "comparison.json").exists()
    assert Path(written["summary"]).is_file()


def test_incomparable_reports_exit_two_with_a_reason(tmp_path):
    result = run_cli(*scenario_args("incomparable_pair"), "--out", str(tmp_path))
    assert result.returncode == 2
    assert "INCOMPARABLE_REPORTS" in result.stderr
    assert result.stdout.strip() == ""

    allowed = run_cli(
        *scenario_args("incomparable_pair"), "--out", str(tmp_path), "--allow-mode-mismatch"
    )
    assert allowed.returncode == 2  # MODE_MISMATCH is a FAIL, not a refusal
    assert "MODE_MISMATCH" in Path(json.loads(allowed.stdout)["written"]["summary"]).read_text()


def test_compare_never_touches_the_private_bridge(tmp_path):
    """The comparator is offline: an unusable bridge origin must not matter."""
    result = run_cli(
        "--bridge",
        "http://192.0.2.10:9999",
        *scenario_args("unchanged"),
        "--out",
        str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    # The same origin is refused for a command that does need the bridge.
    live = run_cli("--bridge", "http://192.0.2.10:9999", "catalog")
    assert live.returncode == 2
    assert "BRIDGE" in live.stderr.upper()


def test_offline_canary_compares_a_fresh_fixture_report_against_the_baseline(tmp_path):
    """The scheduled canary path: rebuild the synthetic report, then compare it."""
    report = run_cli(
        "report",
        "--tickers",
        "TSTA,TSTB",
        "--as-of",
        "2026-10-11",
        "--out",
        str(tmp_path / "report"),
        "--fixture",
        str(FIXTURE_TREE),
    )
    assert report.returncode == 0, report.stderr
    fresh = tmp_path / "report" / "report.json"
    assert fresh.is_file()

    compared = run_cli(
        "compare",
        "--baseline",
        str(BASELINE),
        "--candidate",
        str(fresh),
        "--out",
        str(tmp_path / "compare"),
        "--expect-severity",
        "PASS",
    )
    assert compared.returncode == 0, compared.stdout + compared.stderr
    payload = json.loads(compared.stdout)
    assert payload["overall_severity"] == "PASS"
    assert payload["status"] == "as_expected"
    assert payload["finding_counts"] == {"PASS": 0, "INFO": 0, "WARNING": 0, "FAIL": 0}
    assert payload["skipped_checks"], "unchanged unusable states must be recorded as not evaluated"

    for key in ("summary", "summary_markdown"):
        text = Path(payload["written"][key]).read_text()
        assert "financial-data-wrds-private" not in text
        assert "sec/bulk/" not in text


def test_cli_summary_of_a_leaky_candidate_stays_clean(tmp_path):
    result = run_cli(*scenario_args("leakage_attempt"), "--out", str(tmp_path))
    assert result.returncode == 0
    written = json.loads(result.stdout)["written"]
    for key in ("summary", "summary_markdown"):
        text = Path(written[key]).read_text()
        for marker in (
            "sec/bulk/",
            "should-never-be-published",
            "leaky-session-token",
            "financial-data-wrds-private",
        ):
            assert marker not in text, f"{marker} in {key}"
