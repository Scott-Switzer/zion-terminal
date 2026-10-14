import importlib
import json

import pytest
from click.testing import CliRunner

cli = importlib.import_module("zion_terminal.private_research")


def row(value="9007199254740993", unit="USD", start="2024-01-01"):
    return dict(
        metric="revenue",
        frequency="annual",
        period_start=start,
        period_end="2024-12-31",
        unit=unit,
        value_decimal=value,
        source_observation_ids=["sec1"],
    )


def test_compare_exact_and_units_and_missingness():
    result = cli.compare_histories(
        {
            "a": {"observations": [row()]},
            "b": {"observations": [row("9007199254740994")]},
            "c": {"observations": [row(unit="EUR")]},
        }
    )
    usd = next(x for x in result["comparisons"] if x["unit"] == "USD")
    assert usd["difference_from_first"]["b"] == {"numerator": "1", "denominator": "1"}
    assert usd["values"]["c"] is None
    assert usd["missing_ciks"] == ["c"]
    assert result["alignment"] == "EXACT_PERIOD_START_END_FREQUENCY_UNIT"


def test_compare_rejects_ambiguous_period_and_inexact_numbers():
    with pytest.raises(Exception, match="AMBIGUOUS"):
        cli.compare_histories({"a": {"observations": [row(), row("2")]}})
    with pytest.raises(Exception, match="EXACT"):
        cli.compare_histories({"a": {"observations": [row(1.1)]}})


def test_compare_cli_pins_release_preserves_provenance_exports(monkeypatch, tmp_path):
    calls = []

    def fetch(base, token, route, params):
        calls.append(dict(params))
        return {
            "release_id": "fixed",
            "observations": [row()],
            "source_records": {"sec1": {"accession": "SEC"}},
            "coverage_gaps": [],
            "pagination": {"next_cursor": None},
        }, 100

    monkeypatch.setattr(cli, "fetch", fetch)
    path = tmp_path / "dataset.json"
    result = CliRunner().invoke(
        cli.research,
        [
            "compare",
            "--ciks",
            "a,b",
            "--metrics",
            "revenue",
            "--frequency",
            "annual",
            "--as-of",
            "2025-03-01",
            "--output",
            str(path),
        ],
        env={"ZION_PRIVATE_API_TOKEN": "secret"},
    )
    assert result.exit_code == 0, result.output
    dataset = json.loads(path.read_text())
    assert calls[1]["expected_release_id"] == "fixed"
    assert dataset["histories"]["a"]["pages"][0]["source_records"]["sec1"]["accession"] == "SEC"
    assert dataset["request"]["as_of"] == "2025-03-01"
    assert "secret" not in path.read_text()


def test_compare_release_drift_has_no_partial_export(monkeypatch, tmp_path):
    releases = iter(["one", "two"])
    monkeypatch.setattr(
        cli,
        "fetch",
        lambda *a: ({"release_id": next(releases), "observations": [row()], "pagination": {}}, 100),
    )
    path = tmp_path / "partial.json"
    result = CliRunner().invoke(
        cli.research,
        ["compare", "--ciks", "a,b", "--frequency", "annual", "--output", str(path)],
        env={"ZION_PRIVATE_API_TOKEN": "secret"},
    )
    assert result.exit_code != 0 and "RELEASE_CHANGED" in result.output
    assert not path.exists()


def test_export_does_not_replace_existing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "fetch", lambda *a: ({"release_id": "one"}, 100))
    path = tmp_path / "dataset.json"
    path.write_text("retain")
    result = CliRunner().invoke(
        cli.research, ["metrics", "--output", str(path)], env={"ZION_PRIVATE_API_TOKEN": "secret"}
    )
    assert result.exit_code != 0 and path.read_text() == "retain"


def test_compare_total_budget_applies_across_companies(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "MAX_TOTAL", 150)
    monkeypatch.setattr(
        cli,
        "fetch",
        lambda *a: ({"release_id": "one", "observations": [row()], "pagination": {}}, 100),
    )
    path = tmp_path / "partial.json"
    result = CliRunner().invoke(
        cli.research,
        ["compare", "--ciks", "a,b", "--frequency", "annual", "--output", str(path)],
        env={"ZION_PRIVATE_API_TOKEN": "secret"},
    )
    assert result.exit_code != 0 and "TOTAL_BUDGET" in result.output
    assert not path.exists()


def test_local_derived_metrics_exact_and_source_linked():
    prior = {
        **row("3", start="2023-01-01"),
        "period_end": "2023-12-31",
        "source_observation_ids": ["prior"],
    }
    current = row("4")
    income = {**row("1"), "metric": "operating_income", "source_observation_ids": ["income"]}
    result = cli.compare_histories({"a": {"observations": [prior, current, income]}})
    margin = next(r for r in result["comparisons"] if r["metric"] == "operating_margin")
    growth = next(r for r in result["comparisons"] if r["metric"] == "revenue_growth")
    assert margin["values"]["a"] == {"numerator": "1", "denominator": "4"}
    assert growth["values"]["a"] == {"numerator": "1", "denominator": "3"}
    assert growth["source_observation_ids"]["a"] == ["prior", "sec1"]


def test_zero_revenue_does_not_invent_margin():
    income = {**row("1"), "metric": "operating_income"}
    result = cli.compare_histories({"a": {"observations": [row("0"), income]}})
    assert not any(r["metric"] == "operating_margin" for r in result["comparisons"])
    assert result["derived_gaps"]["a"][0]["reason"] == "ZERO_REVENUE_DENOMINATOR"


def test_workflow_release_reads_history_provenance():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts/sec_research_workflow.py"
    spec = importlib.util.spec_from_file_location("workflow", path)
    workflow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workflow)
    assert (
        workflow.response_release({"pages": [{"provenance": {"release_id": "fixed"}}]}) == "fixed"
    )


def test_compare_reports_effective_server_cutoff_when_not_supplied(monkeypatch):
    monkeypatch.setattr(
        cli,
        "fetch",
        lambda *a: (
            {
                "release_id": "fixed",
                "as_of": "2026-09-24T00:00:00Z",
                "observations": [row()],
                "pagination": {},
            },
            100,
        ),
    )
    result = CliRunner().invoke(
        cli.research,
        ["compare", "--ciks", "a,b", "--frequency", "annual"],
        env={"ZION_PRIVATE_API_TOKEN": "secret"},
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["as_of"] == "2026-09-24T00:00:00Z"
