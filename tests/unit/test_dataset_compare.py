"""Regression comparison: severities, keying and the leakage guard.

The scenarios are the committed synthetic report pairs under
``tests/fixtures/datasets/comparisons``. Expectations here are hand-declared, not
generated from the implementation: each scenario names the codes and severities
the documented taxonomy requires, plus codes it must never produce.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zion_terminal.datasets.compare import (
    COMPARISON_SCHEMA,
    EXIT_STATUS,
    IncomparableReports,
    LeakageError,
    assert_no_private_metadata,
    compare_reports,
    load_report,
    sanitize_comparison,
    write_comparison,
)

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "tests" / "fixtures" / "datasets" / "comparisons"
CLOCK = lambda: "2026-10-12T00:00:00+00:00"  # noqa: E731 - fixed clock for digests


def run_scenario(name: str, **kwargs) -> dict:
    return compare_reports(
        load_report(SCENARIOS / name / "baseline.json"),
        load_report(SCENARIOS / name / "candidate.json"),
        clock=CLOCK,
        **kwargs,
    )


def codes(result: dict, *, ticker: str | None = None, severity: str | None = None) -> list[str]:
    found: list[str] = []
    for company in result["companies"]:
        if ticker is not None and company["ticker"] != ticker:
            continue
        for item in company["findings"]:
            if severity is None or item["severity"] == severity:
                found.append(item["code"])
    for item in result["report_level"]["findings"]:
        if ticker is not None:
            continue
        if severity is None or item["severity"] == severity:
            found.append(item["code"])
    return found


def finding(result: dict, code: str) -> dict:
    for company in result["companies"]:
        for item in company["findings"]:
            if item["code"] == code:
                return item
    for item in result["report_level"]["findings"]:
        if item["code"] == code:
            return item
    raise AssertionError(f"{code} not present in {codes(result)}")


# -- expectations, declared per scenario -----------------------------------
#: ``content_address`` records whether the candidate content address is expected
#: to have moved; that move is INFO on its own and never drives the severity.
CASES: dict[str, dict] = {
    "unchanged": {"overall": "PASS", "total": 0, "content_address": "same"},
    "new_period": {
        "overall": "INFO",
        "content_address": "changed",
        "include": [("PERIOD_OBSERVED_ADDED", "INFO"), ("NEW_PERIOD_FIELD_OBSERVED", "INFO")],
        "exclude": ["VALUE_CHANGED_WITHOUT_SOURCE_REVISION", "PERIOD_OBSERVED_LOST"],
    },
    "restatement": {
        "overall": "INFO",
        "content_address": "changed",
        "include": [("RESTATEMENT_WITH_NEW_FILING", "INFO")],
        "exclude": [
            "VALUE_CHANGED_WITHOUT_SOURCE_REVISION",
            "FILING_REGRESSION_EARLIER_ACCESSION",
            "FUTURE_AVAILABILITY_UNSUPPORTED",
        ],
    },
    "field_lost": {
        "overall": "WARNING",
        "include": [("FIELD_LOST", "WARNING")],
    },
    "observation_lost": {
        "overall": "WARNING",
        "include": [("OBSERVATION_LOST", "WARNING")],
    },
    "debt_concept_demoted": {
        "overall": "WARNING",
        "include": [("CONCEPT_PRIORITY_DEMOTED", "WARNING")],
        "exclude": ["PROHIBITED_BASIS_SUBSTITUTION"],
    },
    "prohibited_substitution": {
        "overall": "FAIL",
        "include": [("PROHIBITED_BASIS_SUBSTITUTION", "FAIL")],
    },
    "invalid_unit_conversion": {
        "overall": "FAIL",
        "include": [("SUSPECTED_UNCONVERTED_SCALE", "FAIL")],
        "exclude": ["VALUE_CHANGED_WITHOUT_SOURCE_REVISION"],
    },
    "contract_unit_violation": {
        "overall": "FAIL",
        "include": [("CONTRACT_UNIT_VIOLATION", "FAIL")],
    },
    "source_version_mismatch": {
        "overall": "WARNING",
        "include": [("VALUE_CHANGED_WITH_SOURCE_REVISION", "WARNING")],
        "exclude": ["VALUE_CHANGED_WITHOUT_SOURCE_REVISION"],
    },
    "unexplained_value_change": {
        "overall": "FAIL",
        "include": [("VALUE_CHANGED_WITHOUT_SOURCE_REVISION", "FAIL")],
    },
    "manifest_integrity_failure": {
        "overall": "FAIL",
        "include": [
            ("SOURCE_FAMILY_UNRESOLVED", "FAIL"),
            ("MANIFEST_ARTIFACT_SAMPLE_INCOMPLETE", "FAIL"),
        ],
    },
    "validation_downgrade": {
        "overall": "WARNING",
        "include": [("VALIDATION_DOWNGRADE", "WARNING")],
    },
    "non_independent_validation": {
        "overall": "FAIL",
        "include": [("NON_INDEPENDENT_CORROBORATION", "FAIL")],
    },
    "forward_availability": {
        "overall": "FAIL",
        "include": [("FUTURE_AVAILABILITY_UNSUPPORTED", "FAIL")],
    },
    "issuer_identity_change": {
        "overall": "FAIL",
        "include": [("ISSUER_IDENTITY_CHANGED", "FAIL")],
    },
    "ticker_collision": {
        "overall": "FAIL",
        "include": [("TICKER_IDENTITY_COLLISION", "FAIL")],
    },
    "blocked_company": {
        "overall": "FAIL",
        "include": [
            ("BLOCKING_ISSUE_ADDED", "FAIL"),
            ("COMPANY_OVERALL_DOWNGRADE", "FAIL"),
            ("PERIOD_OBSERVED_LOST", "WARNING"),
        ],
    },
    "as_of_regression": {
        "overall": "WARNING",
        "include": [("AS_OF_REGRESSION", "WARNING")],
    },
    "sanitized_pair": {
        "overall": "WARNING",
        "include": [("VALIDATION_DOWNGRADE", "WARNING")],
        "skipped": True,
    },
    "leakage_attempt": {
        "overall": "INFO",
        "content_address": "changed",
        "exclude": ["VALUE_CHANGED_WITHOUT_SOURCE_REVISION", "MANIFEST_ARTIFACT_SAMPLE_INCOMPLETE"],
    },
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_scenario_severity_and_codes(name: str):
    case = CASES[name]
    result = run_scenario(name)
    assert result["schema_version"] == COMPARISON_SCHEMA
    assert result["overall_severity"] == case["overall"], codes(result)
    moved = "REPORT_CONTENT_ADDRESS_CHANGED" in codes(result)
    assert moved == (case.get("content_address", "changed") == "changed"), codes(result)
    if "total" in case:
        assert result["findings_total"] == case["total"]
    for code, severity in case.get("include", []):
        item = finding(result, code)
        assert item["severity"] == severity, f"{code} severity {item['severity']} != {severity}"
    for code in case.get("exclude", []):
        assert code not in codes(result), f"{code} must not be produced"
    if case.get("skipped"):
        assert result["skipped_checks"]


def test_documented_exit_status_mapping():
    assert EXIT_STATUS == {"PASS": 0, "INFO": 0, "WARNING": 1, "FAIL": 2}
    for name, case in CASES.items():
        result = run_scenario(name)
        assert EXIT_STATUS[result["overall_severity"]] == EXIT_STATUS[case["overall"]]


def test_no_comparison_of_identical_reports_reports_a_content_address_change():
    result = run_scenario("unchanged")
    assert "REPORT_CONTENT_ADDRESS_CHANGED" not in codes(result)


def test_a_moved_content_address_alone_never_exceeds_info():
    result = run_scenario("leakage_attempt")
    # The evaluated content is identical; only the content address moved.
    assert codes(result) == ["REPORT_CONTENT_ADDRESS_CHANGED"]
    assert result["overall_severity"] == "INFO"
    assert EXIT_STATUS[result["overall_severity"]] == 0


def test_thresholds_are_explicit_in_the_result():
    result = run_scenario("unexplained_value_change")
    assert result["thresholds"]["value_drift_relative"] == "0.0005"
    assert result["thresholds"]["value_drift_absolute"] == "1000000"
    assert result["thresholds"]["removal_rule"].startswith("a change is immaterial")


def test_immaterial_change_is_info_not_failure(tmp_path):
    baseline = load_report(SCENARIOS / "unchanged" / "baseline.json")
    candidate = json.loads(json.dumps(baseline))
    entry = candidate["companies"][0]["contract"]["fields"]["2025-12-31"]["revenue"]
    entry["value"] = "100000500000"  # 0.0005% of 100B, USD 500k absolute
    entry["observation"]["value"] = "100000500000"
    result = compare_reports(baseline, candidate, clock=CLOCK)
    assert "IMMATERIAL_VALUE_DRIFT" in codes(result)
    assert result["overall_severity"] == "INFO"


def test_sign_convention_violation_is_a_failure():
    baseline = load_report(SCENARIOS / "unchanged" / "baseline.json")
    candidate = json.loads(json.dumps(baseline))
    entry = candidate["companies"][0]["contract"]["fields"]["2025-12-31"]["revenue"]
    entry["value"] = "-100000000000"
    entry["observation"]["value"] = "-100000000000"
    result = compare_reports(baseline, candidate, clock=CLOCK)
    assert "SIGN_CONVENTION_VIOLATION" in codes(result)
    assert result["overall_severity"] == "FAIL"


def test_earlier_accession_for_the_same_period_is_a_failure():
    baseline = load_report(SCENARIOS / "unchanged" / "baseline.json")
    candidate = json.loads(json.dumps(baseline))
    entry = candidate["companies"][0]["contract"]["fields"]["2025-12-31"]["revenue"]
    entry["value"] = "104000000000"
    entry["observation"]["value"] = "104000000000"
    entry["observation"]["accession"] = "0000000001-24-000001"
    entry["observation"]["filed"] = "2024-01-30"
    result = compare_reports(baseline, candidate, clock=CLOCK)
    assert "FILING_REGRESSION_EARLIER_ACCESSION" in codes(result)
    assert result["overall_severity"] == "FAIL"


def test_availability_cutoff_cannot_move_earlier_for_one_accession():
    baseline = load_report(SCENARIOS / "unchanged" / "baseline.json")
    candidate = json.loads(json.dumps(baseline))
    entry = candidate["companies"][0]["contract"]["fields"]["2025-12-31"]["revenue"]
    entry["observation"]["available_at"] = "2026-01-30T04:00:00Z"
    result = compare_reports(baseline, candidate, clock=CLOCK)
    assert "AVAILABILITY_CUTOFF_INCONSISTENT" in codes(result)
    assert result["overall_severity"] == "FAIL"


def test_preexisting_non_independent_corroboration_is_a_warning_not_a_silent_pass():
    baseline = load_report(SCENARIOS / "unchanged" / "baseline.json")
    for company in baseline["companies"]:
        company["source_identity"] = [
            item for item in company["source_identity"] if item["state"] == "MATCH"
        ]
    result = compare_reports(baseline, json.loads(json.dumps(baseline)), clock=CLOCK)
    assert result["overall_severity"] == "WARNING"
    assert all(code == "NON_INDEPENDENT_CORROBORATION_PREEXISTING" for code in codes(result))


def test_incomparable_inputs_are_refused_unless_explicitly_allowed():
    with pytest.raises(IncomparableReports):
        run_scenario("incomparable_pair")
    with pytest.raises(IncomparableReports):
        run_scenario("contract_mismatch")
    allowed = run_scenario("incomparable_pair", allow_mode_mismatch=True)
    assert "MODE_MISMATCH" in codes(allowed)
    assert allowed["overall_severity"] == "FAIL"
    allowed = run_scenario("contract_mismatch", allow_contract_mismatch=True)
    assert "CONTRACT_SCHEMA_MISMATCH" in codes(allowed)


def test_summary_inputs_degrade_to_the_public_fields_only():
    result = run_scenario("sanitized_pair")
    assert result["baseline"]["detail_level"] == "sanitized_summary"
    assert result["candidate"]["provenance_available"] is False
    assert any("sanitized input" in reason for reason in result["skipped_checks"])
    assert "VALUE_CHANGED_WITHOUT_SOURCE_REVISION" not in codes(result)


def test_one_report_digest_moves_without_changing_the_severity():
    baseline = load_report(SCENARIOS / "unchanged" / "baseline.json")
    candidate = json.loads(json.dumps(baseline))
    candidate["generated_at"] = "2026-10-13T00:00:00+00:00"
    candidate["report_sha256"] = "f" * 64
    result = compare_reports(baseline, candidate, clock=CLOCK)
    assert "REPORT_CONTENT_ADDRESS_CHANGED" in codes(result)
    assert result["overall_severity"] == "INFO"


def test_stable_observation_key_means_a_new_observation_is_not_a_restatement():
    baseline = load_report(SCENARIOS / "unchanged" / "baseline.json")
    candidate = json.loads(json.dumps(baseline))
    entry = candidate["companies"][0]["contract"]["fields"]["2025-12-31"]["revenue"]
    # Same value and filing, but a different unit and therefore a different observation.
    entry["unit"] = "shares"
    entry["observation"]["unit"] = "shares"
    result = compare_reports(baseline, candidate, clock=CLOCK)
    assert "CONTRACT_UNIT_VIOLATION" in codes(result)
    assert result["overall_severity"] == "FAIL"


# -- leakage guard ---------------------------------------------------------
def test_sanitized_comparison_never_carries_private_provenance(tmp_path):
    result = run_scenario("leakage_attempt")
    written = write_comparison(result, tmp_path)
    markers = (
        "sec/bulk/sec-nightly-20260924",
        "financial-data-wrds-private",
        "financial-system-datasets",
        "should-never-be-published",
        "leaky-session-token",
        "leaky-etag-27",
        "b" * 64,
    )
    for key in ("summary", "summary_markdown"):
        text = Path(written[key]).read_text()
        assert_no_private_metadata(text, allowed_digests=(result["comparison_sha256"],))
        for marker in markers:
            assert marker not in text, f"{marker} leaked into {key}"
    # The private comparison is the artifact that keeps object identity, while the
    # sanitized pair must not. A value change is what makes that contrast visible.
    leaky = run_scenario("unexplained_value_change")
    candidate = load_report(SCENARIOS / "unexplained_value_change" / "candidate.json")
    for company in candidate["companies"]:
        for period_fields in company["contract"]["fields"].values():
            for entry in period_fields.values():
                observation = entry.get("observation")
                if observation and observation.get("source"):
                    observation["source"]["path"] = "sec/bulk/leaky/companyfacts.zip"
                    observation["source"]["sha256"] = "c" * 64
    baseline = load_report(SCENARIOS / "unexplained_value_change" / "baseline.json")
    leaky = compare_reports(baseline, candidate, clock=CLOCK)
    leaky_written = write_comparison(leaky, tmp_path / "leaky")
    private = Path(leaky_written["comparison"]).read_text()
    assert "sec/bulk/leaky/companyfacts.zip" in private
    assert "c" * 64 in private
    for key in ("summary", "summary_markdown"):
        text = Path(leaky_written[key]).read_text()
        assert "sec/bulk/leaky" not in text
        assert "c" * 64 not in text


def test_leakage_guard_rejects_private_markers():
    for payload in (
        "store path sec/bulk/sec-nightly-20260924/companyfacts.zip",
        "sha256 " + "a" * 64,
        '"sha256":"deadbeef"',
        "s3://financial-data-wrds-private/restricted/quarterly.parquet",
        "research bucket ppe-sec-intelligence-prod",
        "api_key: abc123",
        "Authorization: Bearer abc.def",
        "member CIK0000320193.json",
    ):
        with pytest.raises(LeakageError):
            assert_no_private_metadata(payload)


def test_leakage_guard_allows_the_comparison_digest():
    result = run_scenario("unchanged")
    payload = json.dumps(sanitize_comparison(result), sort_keys=True)
    assert_no_private_metadata(payload, allowed_digests=(result["comparison_sha256"],))
    with pytest.raises(LeakageError):
        assert_no_private_metadata(payload)


def test_summary_only_mode_writes_no_private_comparison(tmp_path):
    result = run_scenario("unexplained_value_change")
    written = write_comparison(result, tmp_path, summary_only=True)
    assert "comparison" not in written
    assert not (tmp_path / "comparison.json").exists()
    assert Path(written["summary"]).is_file()
    assert "VALUE_CHANGED_WITHOUT_SOURCE_REVISION" in Path(written["summary"]).read_text()


def test_comparison_digest_is_reproducible_and_covers_the_content():
    first = run_scenario("restatement")
    second = run_scenario("restatement")
    assert first["comparison_sha256"] == second["comparison_sha256"]
    mutated = json.loads(json.dumps(first))
    mutated["overall_severity"] = "PASS"
    from zion_terminal.datasets.sec_fundamentals import frame_report_hash
    from zion_terminal.datasets.compare import _digest_body

    assert frame_report_hash(_digest_body(mutated)) != first["comparison_sha256"]
