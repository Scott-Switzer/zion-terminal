"""Exercise the documented dataset CLI, not only the underlying library API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "datasets" / "tree"


def run_cli(*args: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "zion_terminal.datasets", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, (
        f"CLI exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("command", "expected_key"),
    [
        (["catalog"], "families"),
        (["discover", "--family", "sec_bulk_archive", "--max-pages", "2"], "quality"),
        (["release", "--family", "serving_release_candidate"], "release_id"),
    ],
)
def test_cli_supports_fixture_after_subcommand(command, expected_key):
    output = run_cli(*command, "--fixture", str(FIXTURES))
    assert expected_key in output


def test_documented_report_command_runs_and_writes_sanitized_output(tmp_path):
    output = run_cli(
        "report",
        "--tickers",
        "TSTA,TSTB",
        "--as-of",
        "2026-10-11",
        "--out",
        str(tmp_path),
        "--fixture",
        str(FIXTURES),
    )
    assert set(output["overall"]) == {"TSTA", "TSTB"}
    assert output["report_sha256"]
    for key in ("report", "markdown", "summary", "summary_markdown", "inventory"):
        assert Path(output["written"][key]).is_file()

    shareable = Path(output["written"]["summary_markdown"]).read_text()
    assert "Fiscal period ending 2025-12-31" in shareable
    assert "sec-filing-intelligence/corpus" not in shareable


def test_global_fixture_before_subcommand_also_works():
    output = run_cli("--fixture", str(FIXTURES), "catalog")
    assert "families" in output
