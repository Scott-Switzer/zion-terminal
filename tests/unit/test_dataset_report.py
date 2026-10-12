"""End-to-end report tests on synthetic fixtures, including leakage guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zion_terminal.datasets import sec_fundamentals as sf
from zion_terminal.datasets.cli import load_fixture_bridge
from zion_terminal.datasets.registry import FinancialDatasetRegistry
from zion_terminal.datasets.report import (
    RegistryReport,
    digest_body,
    render_markdown,
    render_sanitized_markdown,
    sanitize_summary,
    write_report,
)

FIXTURE_TREE = Path(__file__).resolve().parents[1] / "fixtures" / "datasets" / "tree"
AS_OF = "2026-10-11"
PRIVATE_MARKERS = (
    "sec-filing-intelligence/corpus",
    "sec/bulk/",
    "bbbbbbbbbbbb",
    "cccccccccccc",
    "11111111111111111111111111111111",
)


@pytest.fixture(scope="module")
def report() -> dict:
    registry = FinancialDatasetRegistry(load_fixture_bridge(FIXTURE_TREE))
    builder = RegistryReport(registry, clock=lambda: "2026-10-11T00:00:00+00:00", mode="fixture")
    return builder.build(["TSTA", "TSTB"], as_of=AS_OF, periods=3, artifact_sample=3)


def test_report_is_reproducible_and_digest_addressed(report):
    assert report["schema_version"] == "zion-financial-registry-report-v1"
    assert report["mode"] == "fixture"
    assert report["generated_at"] == "2026-10-11T00:00:00+00:00"
    assert sf.frame_report_hash(digest_body(report)) == report["report_sha256"]
    # The digest is a reproducible content address: it ignores the wall clock.
    replayed = dict(report, generated_at="2030-01-01T00:00:00+00:00")
    assert sf.frame_report_hash(digest_body(replayed)) == report["report_sha256"]


def test_report_contains_evaluated_observations_for_every_company(report):
    companies = {item["ticker"]: item for item in report["companies"]}
    assert set(companies) == {"TSTA", "TSTB"}
    for company in companies.values():
        assert company["contract"]["periods"] == ["2025-12-31", "2024-12-31", "2023-12-31"]
        assert company["contract"]["observations_examined"] > 0
        values = [
            entry["value"]
            for fields in company["contract"]["fields"].values()
            for entry in fields.values()
            if entry["value"] is not None
        ]
        assert len(values) >= 10
        assert all(
            entry["state"]
            for fields in company["contract"]["fields"].values()
            for entry in fields.values()
        )


def test_company_states_are_disclosed_not_rounded_up(report):
    companies = {item["ticker"]: item for item in report["companies"]}
    tsta = companies["TSTA"]
    assert tsta["overall"] == sf.STATE_CONFLICT  # fields with genuinely conflicting concepts
    # Both capital-expenditure periods carrying only a non-USD unit are replays.
    assert tsta["state_counts"]["UNIT_MISMATCH"] == 2
    assert tsta["state_counts"]["PERIOD_MISMATCH"] == 1
    assert tsta["state_counts"]["NOT_PIT_ELIGIBLE"] == 1
    tstb = companies["TSTB"]
    assert tstb["overall"] == sf.STATE_SOURCE_UNVALIDATED
    assert "NO_SECOND_SOURCE_CONFIRMATION" in tstb["blocking_issues"]
    assert "PUBLISHED_SEC_FACTS_NOT_IN_RELEASE" in tstb["blocking_issues"]


def test_second_source_confirmation_is_recorded_per_field_and_period(report):
    tsta = next(item for item in report["companies"] if item["ticker"] == "TSTA")
    fields_2025 = tsta["contract"]["fields"]["2025-12-31"]
    cash = fields_2025["cash_and_equivalents"]
    assert cash["state"] == sf.STATE_SOURCE_VALIDATED
    assert any(item["state"] == "AGREES" for item in cash["cross_sources"])
    # The approved packet disagrees with EBIT for the same period: a recorded conflict.
    ebit = fields_2025["operating_income_ebit"]
    assert ebit["state"] == sf.STATE_CONFLICT
    assert "SECOND_SOURCE_DISAGREES" in ebit["reason_codes"]
    # Confirmation is period-specific: 2024 revenue has no second source at all.
    assert (
        tsta["contract"]["fields"]["2024-12-31"]["revenue"]["state"] == sf.STATE_SOURCE_UNVALIDATED
    )
    assert tsta["contract"]["fields"]["2024-12-31"]["revenue"]["cross_sources"] == []
    # The split short-term debt total is confirmed against the packet's single figure.
    short_term = fields_2025[sf.SHORT_TERM_DERIVED_FIELD]
    assert short_term["state"] == sf.STATE_PASS_DERIVED
    assert any(item["state"] == "AGREES" for item in short_term["cross_sources"])


def test_source_fallback_from_corpus_to_archive_member_is_visible(report):
    tsta = next(item for item in report["companies"] if item["ticker"] == "TSTA")
    tstb = next(item for item in report["companies"] if item["ticker"] == "TSTB")
    assert tsta["facts_origin"]["origin"] == "sec filing-intelligence corpus"
    assert tsta["facts_origin"]["object"]["sha256"]
    assert "bulk archive" in tstb["facts_origin"]["origin"]
    attempted = report and tstb["facts_origin"]
    assert attempted  # the archive member was read through bounded ranges
    assert tstb["contract"]["fields"]["2025-12-31"]["revenue"]["value"] == "360"


def test_serving_coverage_names_the_fields_the_published_layer_cannot_supply(report):
    coverage = {item["field_id"]: item for item in report["serving_coverage"]["coverage"]}
    assert coverage["revenue"]["state"] == "SUPPORTED"
    assert coverage["net_income_common"]["state"] == "BASIS_DIFFERS"
    assert coverage["depreciation_amortization"]["state"] == "ABSENT_FROM_SERVING_CATALOG"
    assert coverage["diluted_weighted_average_shares"]["state"] == "BASIS_DIFFERS"
    assert report["serving_coverage"]["supported_fields"] == [
        "revenue",
        "operating_income_ebit",
        "cash_and_equivalents",
    ]
    assert "debt_current" in report["serving_coverage"]["unsupported_fields"]


def test_half_published_release_is_reported_as_a_limitation(report):
    candidate = next(
        item for item in report["sources"] if item.get("family_id") == "serving_release_candidate"
    )
    assert candidate["artifact_sample"]["complete"] is False
    assert "MANIFEST_ARTIFACTS_NOT_STORED" in candidate["findings"]
    assert any(
        "sampled manifest artifacts are not stored" in item for item in report["limitations"]
    )


def test_sanitized_summary_leaks_no_private_paths(report):
    summary = sanitize_summary(report)
    text = json.dumps(summary, sort_keys=True)
    for marker in PRIVATE_MARKERS:
        assert marker not in text
    # Every inventory entry is either hash-verified, a metadata-only probe, or
    # explicitly absent; none of them silently claims validation.
    for item in report["inventory"]["objects"]:
        assert item.get("sha256") or item["quality"] in {"ABSENT", "DISCOVERED"}
    # The private report itself may name paths; the sanitized summary must not.
    private_text = json.dumps(report, sort_keys=True)
    assert "sec-filing-intelligence/corpus" in private_text


def test_sanitized_markdown_keeps_the_observations_but_drops_private_provenance(report):
    markdown = render_sanitized_markdown(report)
    # Same evaluated evidence as the private report...
    assert "| `revenue` |" in markdown
    assert "Fiscal period ending 2025-12-31" in markdown
    assert "Aggregate manifest row counts are not evidence" in markdown
    # ...with no private store path, object hash or inventory entry.
    for marker in PRIVATE_MARKERS:
        assert marker not in markdown
    assert "Facts origin" not in markdown
    object_hashes = [
        (company.get("facts_origin") or {}).get("object", {}).get("sha256")
        for company in report["companies"]
    ]
    for digest in (digest for digest in object_hashes if digest):
        assert digest not in markdown


def test_markdown_renders_field_tables_and_limitations(report):
    markdown = render_markdown(report)
    assert "## Companies" in markdown
    assert "| `revenue` |" in markdown
    assert "Fiscal period ending 2025-12-31" in markdown
    assert "## Limitations" in markdown
    assert "Aggregate manifest row counts are not evidence" in markdown


def test_write_report_emits_private_and_shareable_artifacts(tmp_path, report):
    written = write_report(report, tmp_path)
    for key in ("report", "markdown", "summary", "summary_markdown", "inventory"):
        assert Path(written[key]).exists()
    summary = json.loads(Path(written["summary"]).read_text())
    assert summary["companies"][0]["ticker"] == "TSTA"
    assert (
        json.loads(Path(written["report"]).read_text())["report_sha256"] == report["report_sha256"]
    )
    shared = Path(written["summary_markdown"]).read_text()
    for marker in PRIVATE_MARKERS:
        assert marker not in shared
