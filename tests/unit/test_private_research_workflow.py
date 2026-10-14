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


def test_table_exact_display_export_stays_json(monkeypatch, tmp_path):
    payload = {
        "release_id": "fixed",
        "as_of": "2026-09-24T00:00:00Z",
        "cik": "0000047111",
        "observations": [row("9007199254740993.0001")],
        "coverage_gaps": [
            {"metric": "free_cash_flow", "period_end": "2024-12-31", "reason": "SOURCE_MISSING"}
        ],
    }
    monkeypatch.setattr(cli, "fetch", lambda *a: (payload, 100))
    path = tmp_path / "table-export.json"
    result = CliRunner().invoke(
        cli.research,
        ["history", "--cik", "0000047111", "--format", "table", "--output", str(path)],
        env={"ZION_PRIVATE_API_TOKEN": "secret"},
    )
    assert result.exit_code == 0, result.output
    for text in [
        "fixed",
        "2026-09-24T00:00:00Z",
        "9007199254740993.0001",
        "USD",
        "2024-12-31",
        "Quality gaps: 1",
        "SOURCE_MISSING",
    ]:
        assert text in result.output
    assert json.loads(path.read_text()) == payload


def test_provenance_table_validates_filing_links_and_missing_records(monkeypatch):
    payload = {
        "release_id": "fixed",
        "as_of": "2026-09-24T00:00:00Z",
        "cik": "0000047111",
        "observations": [{**row(), "source_observation_ids": ["valid", "invalid", "absent"]}],
        "source_records": {
            "valid": {
                "cik": "0000047111",
                "accession": "0001628280-26-008586",
                "period_end": "2024-12-31",
                "unit": "USD",
            },
            "invalid": {"cik": "0000047111", "accession": "../../evil"},
        },
        "pagination": {},
    }
    monkeypatch.setattr(cli, "fetch", lambda *a: (payload, 100))
    result = CliRunner().invoke(
        cli.research,
        ["provenance", "--cik", "0000047111", "--format", "table"],
        env={"ZION_PRIVATE_API_TOKEN": "secret"},
    )
    assert result.exit_code == 0, result.output
    assert (
        "https://www.sec.gov/Archives/edgar/data/47111/0001628280-26-008586-index.html"
        in result.output
    )
    assert "LINK_MISSING_INVALID_CIK_OR_ACCESSION" in result.output
    assert "SOURCE_RECORD_MISSING" in result.output
    assert "../../evil" not in result.output


def test_table_compare_rational_and_missingness():
    value = {
        "release_id": "fixed",
        "as_of": "cutoff",
        "comparisons": [
            {
                "metric": "operating_margin",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "unit": "ratio",
                "values": {"a": {"numerator": "1", "denominator": "3"}, "b": None},
                "difference_from_first": {"a": {"numerator": "0", "denominator": "1"}},
            }
        ],
        "derived_gaps": {"b": [{"metric": "operating_margin", "reason": "MISSING_REVENUE"}]},
    }
    rendered = cli.render_table(value, "compare")
    assert "1/3" in rendered and "MISSING" in rendered and "Quality gaps: 1" in rendered


def test_source_link_rejects_untrusted_identifiers():
    assert cli.sec_filing_index("0", "0001628280-26-008586") is None
    assert cli.sec_filing_index("0000047111", "000162828026008586") is None
    assert cli.sec_filing_index("47111/evil", "0001628280-26-008586") is None


def test_table_coverage_batch_failure_and_complete_collections():
    coverage = {
        "release_id": "fixed",
        "cik": "0000047111",
        "coverage": {"annual": {"revenue": {"observations": 6, "gaps": 2}}},
        "metrics": {"revenue": {"unit": "USD"}},
    }
    rendered = cli.render_table(coverage, "coverage")
    assert (
        "annual:revenue" in rendered
        and "observations=6" in rendered
        and "Quality gaps: 2" in rendered
    )
    batch = {
        "release_id": "fixed",
        "companies": [
            {
                "cik": "0000047111",
                "http_status": 404,
                "result": {"error": {"code": "CIK_NOT_FOUND"}},
            }
        ],
    }
    rendered = cli.render_table(batch, "batch")
    assert "CIK_NOT_FOUND" in rendered and "404" in rendered
    comparison = {
        "histories": {
            "a": {
                "complete": True,
                "pages": [{"pagination": {"next_cursor": "used"}}, {"pagination": {}}],
            }
        }
    }
    assert "MORE_PAGES_AVAILABLE" not in cli.render_table(comparison, "compare")


def test_compare_rejects_different_company_cutoffs_without_partial_export(monkeypatch, tmp_path):
    def fetch(base, token, route, params):
        return {
            "release_id": "same",
            "as_of": "2026-09-24T00:00:00Z" if params["cik"] == "a" else "2026-10-13T00:00:00Z",
            "observations": [row()],
            "coverage_gaps": [],
            "pagination": {"next_cursor": None},
        }, 100

    monkeypatch.setattr(cli, "fetch", fetch)
    output = tmp_path / "mixed.json"
    result = CliRunner().invoke(
        cli.research,
        ["compare", "--ciks", "a,b", "--frequency", "annual", "--output", str(output)],
        env={"ZION_PRIVATE_API_TOKEN": "secret"},
    )
    assert result.exit_code != 0 and "COMPARE_REQUIRES_COMMON_AVAILABILITY_CUTOFF" in result.output
    assert not output.exists()
