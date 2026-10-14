"""Native aggregate-only audit of public SEC metric certificates. No market/provider requests."""

import argparse
import hashlib
import json
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

METRICS = (
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "capital_expenditures",
    "cash",
    "total_debt",
    "diluted_shares",
    "free_cash_flow",
)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def row_key(row):
    return tuple(row.get(k) for k in ("metric", "frequency", "period_start", "period_end", "unit"))


def row_sources(row):
    return {r["source_id"] for r in row.get("source_evidence", [])} | set(
        row.get("accounting_evidence", {}).get("component_ids", {}).values()
    )


def validate_company(body, companion, research_release, financial_release, expected_current=None):
    errors = []
    refinements = []
    for key, expected in [
        ("research_release_id", research_release),
        ("release_id", financial_release),
        ("as_of", companion["as_of"]),
    ]:
        if body.get(key) != expected:
            errors.append("LIVE_" + key.upper() + "_MISMATCH")
    observations = body.get("observations", [])
    if (
        body.get("next_cursor") is not None
        or body.get("total_rows") != len(observations)
        or body.get("coverage_complete_in_this_response") is not True
    ):
        errors.append("PAGINATION_NOT_COMPLETE")
    baseline_rows = {row_key(r): r for r in companion["certification"]["observations"]}
    expected_rows = (
        {row_key(r): r for r in expected_current["observations"]}
        if expected_current
        else baseline_rows
    )
    actual_rows = {row_key(r): r for r in observations}
    if len(actual_rows) != len(observations) or set(actual_rows) != set(expected_rows):
        errors.append("LIVE_ROW_COVERAGE_MISMATCH")
    records = body.get("source_records", {})
    statuses = Counter()
    referenced = set()
    cutoff = datetime.fromisoformat(companion["as_of"].replace("Z", "+00:00"))
    for key, row in actual_rows.items():
        statuses[row.get("status", "MISSING")] += 1
        prior = expected_rows.get(key)
        mismatch = prior and any(
            row.get(field) != prior.get(field)
            for field in ("value_decimal", "status", "verified_statuses", "independent_status")
        )
        if prior and row.get("accounting_status") != prior.get("accounting_status"):
            mismatch = True
        baseline = baseline_rows.get(key)
        if (
            expected_current
            and baseline
            and (
                any(
                    row.get(field) != baseline.get(field)
                    for field in (
                        "value_decimal",
                        "status",
                        "verified_statuses",
                        "independent_status",
                    )
                )
                or row_sources(row) != row_sources(baseline)
            )
        ):
            mismatch = True
        if (
            expected_current
            and baseline
            and row.get("accounting_status") != baseline.get("accounting_status")
        ):
            safe_verified_refinement = (
                baseline.get("accounting_status") == "NOT_ESTABLISHED"
                and row.get("accounting_status") == "UNAVAILABLE"
                and row.get("reasons")
                == prior.get("reasons")
                == ["ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH"]
                and "ACCOUNTING_CERTIFIED" not in row.get("verified_statuses", [])
                and not row.get("accounting_evidence")
                and all(
                    row.get(field) == baseline.get(field)
                    for field in (
                        "value_decimal",
                        "status",
                        "verified_statuses",
                        "independent_status",
                    )
                )
                and row_sources(row) == row_sources(baseline)
                and not mismatch
            )
            if safe_verified_refinement:
                refinements.append(
                    dict(
                        metric=row["metric"],
                        period_end=row["period_end"],
                        baseline_accounting_status="NOT_ESTABLISHED",
                        current_offline_and_live_accounting_status="UNAVAILABLE",
                        reason="ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH",
                    )
                )
            else:
                mismatch = True
        if mismatch:
            errors.append("LIVE_CERTIFICATE_VALUE_OR_STATUS_MISMATCH")
        ids = set(row.get("source_ids", []))
        referenced.update(ids)
        if not ids or ids != row_sources(row) or not ids.issubset(records):
            errors.append("LIVE_SOURCE_LINEAGE_INCOMPLETE")
        if prior and ids != row_sources(prior):
            errors.append("LIVE_SOURCE_ID_SET_MISMATCH")
        for evidence in row.get("source_evidence", []):
            record = records.get(evidence["source_id"], {})
            for field in (
                "concept",
                "accession",
                "form",
                "available_at",
                "unit",
                "currency",
                "period_start",
                "period_end",
                "member_sha256",
            ):
                if evidence.get(field) != record.get(field):
                    errors.append("LIVE_SOURCE_EVIDENCE_RECORD_MISMATCH")
    for sid in referenced:
        record = records.get(sid, {})
        if (
            record.get("source_observation_id") != sid
            or record.get("cik") != companion["cik"]
            or record.get("member_sha256") != companion["member_sha256"]
        ):
            errors.append("LIVE_SOURCE_MEMBER_OR_IDENTITY_MISMATCH")
        available = record.get("available_at")
        if not available or datetime.fromisoformat(available.replace("Z", "+00:00")) > cutoff:
            errors.append("LIVE_SOURCE_AFTER_CUTOFF")
        if not isinstance(record.get("value_decimal"), str):
            errors.append("LIVE_SOURCE_DECIMAL_STRING_REQUIRED")
    if set(records) != referenced:
        errors.append("LIVE_UNREQUESTED_SOURCE_RECORDS")
    return dict(
        cik=companion["cik"],
        observations=len(observations),
        status_counts=dict(statuses),
        expected_observations=len(expected_rows),
        expected_status_counts=dict(Counter(r["status"] for r in expected_rows.values())),
        source_ids=len(referenced),
        errors=sorted(set(errors)),
        status="PASS" if not errors else "FAIL",
        as_of=companion["as_of"],
        pagination_complete=body.get("coverage_complete_in_this_response") is True,
        coverage_gaps=len(body.get("coverage_gaps", [])),
        expected_coverage_gaps=len(companion["certification"]["coverage_gaps"]),
        fail_closed_accounting_refinements=refinements,
    )


def audit(
    publication_dir, certification_dir, output, request, expected_current_certifications=None
):
    publication = json.loads((publication_dir / "publication.json").read_bytes())
    root_raw = (publication_dir / "manifest.json").read_bytes()
    if (
        hashlib.sha256(root_raw).hexdigest() != publication["root"]["sha256"]
        or len(root_raw) != publication["root"]["bytes"]
    ):
        raise ValueError("ROOT_HASH_MISMATCH")
    root = json.loads(root_raw)
    summary_path = publication_dir / "companion-summary.json"
    if not summary_path.exists():
        summary_path = certification_dir / "companion-summary.json"
    summary_raw = summary_path.read_bytes()
    if (
        hashlib.sha256(summary_raw).hexdigest() != root["certification_summary"]["sha256"]
        or len(summary_raw) != root["certification_summary"]["bytes"]
    ):
        raise ValueError("SUMMARY_HASH_MISMATCH")
    summary = json.loads(summary_raw)
    if set(root["certifications"]) != {r["cik"] for r in summary["companies"]}:
        raise ValueError("EXPECTED_COHORT_MISMATCH")
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    before, measure = request("/v3/capabilities")
    if measure["http_status"] != 200:
        raise ValueError("CAPABILITIES_UNAVAILABLE")
    initial_version = before.get("worker_version_id")
    if not initial_version:
        raise ValueError("WORKER_VERSION_NOT_REPORTED")

    def one(cik):
        raw = (certification_dir / (cik + ".companion.json")).read_bytes()
        ref = root["certifications"][cik]
        if hashlib.sha256(raw).hexdigest() != ref["sha256"] or len(raw) != ref["bytes"]:
            raise ValueError("LOCAL_COMPANION_HASH_MISMATCH")
        companion = json.loads(raw)
        body, measurement = request(
            "/private/v1/research-certification",
            dict(
                cik=cik,
                frequency="annual",
                metrics=",".join(METRICS),
                limit=1000,
                as_of=companion["as_of"],
                date_from="2014-01-01",
                date_to=companion["as_of"][:10],
                expected_release_id=root["financial_release_id"],
            ),
        )
        path = output / (cik + ".json")
        path.write_bytes(encode(body))
        path.chmod(0o600)
        if measurement["http_status"] != 200:
            return dict(
                cik=cik,
                status="FAIL",
                errors=["HTTP_" + str(measurement["http_status"])],
                observations=0,
                expected_observations=len(companion["certification"]["observations"]),
                expected_status_counts=dict(
                    Counter(r["status"] for r in companion["certification"]["observations"])
                ),
                status_counts={},
                source_ids=0,
                measurement=measurement,
            )
        expected_current = (expected_current_certifications or {}).get(cik)
        if expected_current:
            binding = expected_current.get("source_bound_recomputation", {})
            if (
                binding.get("materialized_ref") != root["financial_entries"][cik]["materialized"]
                or binding.get("companion_sha256") != ref["sha256"]
                or binding.get("helper_sha256")
                != root["implementation_refs"]["financial_certification.py"]["sha256"]
                or binding.get("member_sha256") != companion["member_sha256"]
            ):
                raise ValueError("CURRENT_OFFLINE_EXPECTATION_NOT_SOURCE_BOUND")
        result = validate_company(
            body, companion, root["release_id"], root["financial_release_id"], expected_current
        )
        result["measurement"] = measurement
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        companies = list(pool.map(one, sorted(root["certifications"])))
    after, after_measure = request("/v3/capabilities")
    totals = Counter()
    for company in companies:
        totals.update(company["status_counts"])
    observations = sum(r["observations"] for r in companies)
    errors = []
    if any(r["status"] != "PASS" for r in companies):
        errors.append("COMPANY_VERIFICATION_FAILURE")
    expected_unique_rows = sum(r.get("expected_observations", 0) for r in companies)
    expected_statuses = Counter()
    for company in companies:
        expected_statuses.update(company.get("expected_status_counts", {}))
    if observations != expected_unique_rows:
        errors.append("AGGREGATE_ROW_COUNT_MISMATCH")
    if totals != expected_statuses:
        errors.append("AGGREGATE_CERTIFICATION_STATUS_MISMATCH")
    live_gaps = sum(r.get("coverage_gaps", 0) for r in companies)
    expected_gaps = sum(r.get("expected_coverage_gaps", 0) for r in companies)
    if live_gaps != expected_gaps or expected_gaps != summary.get(
        "coverage_gap_count", expected_gaps
    ):
        errors.append("COVERAGE_GAP_COUNT_MISMATCH")
    declared_rows = summary["union_current_series_observations"]
    declared_accounting = summary["union_accounting_certified_observations"]
    if (
        declared_rows != expected_unique_rows
        or declared_accounting != expected_statuses["ACCOUNTING_CERTIFIED"]
    ):
        errors.append("DECLARED_SUMMARY_UNION_COUNT_MISMATCH")
    if after_measure["http_status"] != 200 or after.get("worker_version_id") != initial_version:
        errors.append("DEPLOYMENT_CHANGED_DURING_AUDIT")
    latencies = sorted(r["measurement"]["latency_ms"] for r in companies)
    report = dict(
        status="PASS" if not errors else "FAIL",
        errors=errors,
        research_release_id=root["release_id"],
        financial_release_id=root["financial_release_id"],
        worker_version_id=initial_version,
        companies_checked=len(companies),
        observations=observations,
        status_counts=dict(totals),
        expected_observations=expected_unique_rows,
        expected_accounting_certified=expected_statuses["ACCOUNTING_CERTIFIED"],
        declared_summary_observations=declared_rows,
        declared_summary_accounting_certified=declared_accounting,
        coverage_gap_count=live_gaps,
        expected_coverage_gap_count=expected_gaps,
        fail_closed_accounting_refinements=sum(
            len(r.get("fail_closed_accounting_refinements", [])) for r in companies
        ),
        resolved_source_id_references=sum(r["source_ids"] for r in companies),
        latency_p50_ms=statistics.median(latencies),
        latency_p95_ms=latencies[int(0.95 * (len(latencies) - 1))],
        normal_cutoff_policy="EXACT_CURRENT_COMPANION_AS_OF;NOT_NEWEST_PROVIDER_UPDATE_CUTOFF",
        public_sec_only=True,
        provider_requests=0,
        price_requests=0,
        licensed_sources=0,
        response_rows_printed=0,
        companies=companies,
    )
    path = output / "aggregate.json"
    path.write_bytes(encode(report))
    path.chmod(0o600)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--publication-dir", type=Path, required=True)
    p.add_argument("--certification-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--current-recomputation-dir", type=Path)
    args = p.parse_args()
    from sec_engine_acceptance import request

    try:
        expected_current = (
            {
                p.stem: json.loads(p.read_bytes())
                for p in args.current_recomputation_dir.glob("[0-9]*.json")
            }
            if args.current_recomputation_dir
            else None
        )
        report = audit(
            args.publication_dir, args.certification_dir, args.output, request, expected_current
        )
        print(json.dumps({k: v for k, v in report.items() if k != "companies"}))
        raise SystemExit(0 if report["status"] == "PASS" else 1)
    except Exception as exc:
        print(json.dumps(dict(status="FAIL", error_type=type(exc).__name__)))
        raise SystemExit(1) from None
