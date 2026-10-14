import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "live_cert_audit", Path(__file__).parents[2] / "scripts/audit_live_financial_certification.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample():
    evidence = dict(
        source_id="sid",
        concept="us-gaap:Revenues",
        accession="0000000001-19-000001",
        form="10-K",
        available_at="2019-01-02T00:00:00Z",
        unit="USD",
        currency="USD",
        period_start="2018-01-01",
        period_end="2018-12-31",
        member_sha256="member",
    )
    row = dict(
        metric="revenue",
        frequency="annual",
        period_start="2018-01-01",
        period_end="2018-12-31",
        unit="USD",
        value_decimal="1.01",
        status="INDEPENDENTLY_RECONCILED",
        verified_statuses=["SOURCE_OBSERVED", "INDEPENDENTLY_RECONCILED"],
        independent_status="PASS",
        accounting_status="NOT_ESTABLISHED",
        source_evidence=[evidence],
    )
    record = {k: v for k, v in evidence.items() if k != "source_id"}
    record.update(source_observation_id="sid", cik="0000000001", value_decimal="1.01")
    companion = dict(
        cik="0000000001",
        as_of="2026-09-24T00:00:00Z",
        member_sha256="member",
        certification={"observations": [row]},
    )
    body = dict(
        research_release_id="research",
        release_id="financial",
        as_of=companion["as_of"],
        observations=[dict(row, source_ids=["sid"])],
        source_records={"sid": record},
        next_cursor=None,
        total_rows=1,
        coverage_complete_in_this_response=True,
    )
    return body, companion


def test_complete_exact_current_certificates_and_lineage():
    body, companion = sample()
    result = m.validate_company(body, companion, "research", "financial")
    assert result["status"] == "PASS" and result["observations"] == 1 and result["source_ids"] == 1


def test_stale_release_or_unresolved_sources_fail():
    body, companion = sample()
    body["research_release_id"] = "stale"
    body["source_records"] = {}
    result = m.validate_company(body, companion, "research", "financial")
    assert result["status"] == "FAIL"
    assert "LIVE_RESEARCH_RELEASE_ID_MISMATCH" in result["errors"]
    assert "LIVE_SOURCE_LINEAGE_INCOMPLETE" in result["errors"]


def test_partial_pagination_or_status_upgrade_fails():
    body, companion = sample()
    body["next_cursor"] = "more"
    body["observations"][0]["status"] = "ACCOUNTING_CERTIFIED"
    result = m.validate_company(body, companion, "research", "financial")
    assert "PAGINATION_NOT_COMPLETE" in result["errors"]
    assert "LIVE_CERTIFICATE_VALUE_OR_STATUS_MISMATCH" in result["errors"]


def test_after_cutoff_or_wrong_member_fails():
    body, companion = sample()
    body["source_records"]["sid"]["available_at"] = "2026-10-01T00:00:00Z"
    body["source_records"]["sid"]["member_sha256"] = "other"
    result = m.validate_company(body, companion, "research", "financial")
    assert "LIVE_SOURCE_AFTER_CUTOFF" in result["errors"]
    assert "LIVE_SOURCE_MEMBER_OR_IDENTITY_MISMATCH" in result["errors"]
