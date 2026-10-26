"""Contract tests for the Zion private DDFI benchmark operations.

Built against synthetic releases that satisfy the exact identity chain
(release_id = sha256 of the canonical manifest without release_id) so the tests
exercise the real verification path without depending on the data/ddfi repo.
"""

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))

import ddfi_benchmarks_api  # noqa: E402
from private_api import OPERATIONS, query_existing  # noqa: E402
from private_financials import PrivateFinancialsError  # noqa: E402

PREFIX = "gold/ddfi-benchmarks/v1/"


class Obj:
    def __init__(self, raw):
        self.raw, self.size = raw, len(raw)

    async def text(self):
        return self.raw.decode()


class Bucket:
    def __init__(self, objects):
        self.objects, self.gets = objects, []

    async def get(self, key):
        self.gets.append(key)
        return self.objects.get(key)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def build_release(dataset_id, region, published_at, records, subject_type="industry"):
    normalized = {
        "schema_version": "damodaran-assumptions-v1",
        "dataset_id": dataset_id,
        "region": region,
        "subject_type": subject_type,
        "record_count": len(records),
        "definitions": {r["measure"]: f"definition of {r['measure']}" for r in records},
        "unit_definitions": {"fraction": "unitless fraction", "multiple": "x multiple"},
        "measurement_period_note": "trailing fiscal-year aggregates as published",
        "records": records,
    }
    normalized_raw = encoded(normalized)
    source_raw = b"original workbook bytes"
    manifest = {
        "schema_version": "damodaran-assumptions-v1",
        "dataset_id": dataset_id,
        "region": region,
        "published_at": published_at,
        "source_url": "https://example.invalid/stern/workbook.xls",
        "provider": "NYU Stern / Aswath Damodaran",
        "files": {
            "source.bin": hashlib.sha256(source_raw).hexdigest(),
            "normalized.json": hashlib.sha256(normalized_raw).hexdigest(),
        },
        "lineage": {
            "ingested_bundle": "DAMODARAN_TEST_BUNDLE",
            "evidence_path": "evidence/damodaran_test/workbook.xls",
            "retrieved_at": published_at,
            "server_last_modified": datetime.fromisoformat(published_at).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            ),
            "stated_publication_date": "2024-01-05",
        },
        "rights": {
            "rights_status": "private_review_pending",
            "entitlement": "owner_private_reference",
            "redistribution_policy": "PROHIBITED_PENDING_REVIEW",
        },
        "measures": sorted({r["measure"] for r in records}),
    }
    release_id = hashlib.sha256(encoded(manifest)).hexdigest()
    manifest["release_id"] = release_id
    return release_id, encoded(manifest), normalized_raw


def record(subject, measure, value, unit="fraction", **extra):
    out = {
        "subject": subject,
        "subject_type": "industry",
        "measure": measure,
        "unit": unit,
        "value_decimal": value,
        "classification": "BENCHMARK_ASSUMPTION_NOT_COMPANY_FACT",
        "source_sheet": "Industry Averages",
        "source_column_index": 10,
        "source_column_header": "Cost of Capital",
    }
    if value is None:
        out["missing"] = True
        out["missing_reason"] = "WORKBOOK_TEXT"
        out["workbook_text"] = "NA"
        out["quarantined"] = True
    out.update(extra)
    return out


def make_env():
    ddfi_benchmarks_api._METADATA_CACHE.clear()
    objects = {}
    releases = []

    jan_id, jan_manifest, jan_norm = build_release(
        "cost_of_capital", "US", "2026-01-12",
        [
            record("Software (System & Application)", "wacc", "0.09344048072400105"),
            record("Software (System & Application)", "tax_rate", "0.24"),
            record("Bank (Money Center)", "wacc", None),
        ],
    )
    jul_id, jul_manifest, jul_norm = build_release(
        "cost_of_capital", "US", "2026-06-01",
        [
            record("Software (System & Application)", "wacc", "0.0955"),
            record("Software (System & Application)", "tax_rate", "0.25"),
        ],
    )
    country_id, country_manifest, country_norm = build_release(
        "country_risk_premiums", "Global", "2026-07-10",
        [
            record("France", "country_risk_premium", "0.0061", subject_type="country",
                   subject_region="Europe"),
            record("France", "equity_risk_premium", "0.0481", subject_type="country",
                   subject_region="Europe"),
        ],
        subject_type="country",
    )
    for release_id, manifest_raw, norm_raw in (
        (jan_id, jan_manifest, jan_norm),
        (jul_id, jul_manifest, jul_norm),
        (country_id, country_manifest, country_norm),
    ):
        entry = {
            "release_id": release_id,
            "dataset_id": json.loads(manifest_raw)["dataset_id"],
            "region": json.loads(manifest_raw)["region"],
            "subject_type": json.loads(norm_raw)["subject_type"],
            "published_at": json.loads(manifest_raw)["published_at"],
            "measures": json.loads(manifest_raw)["measures"],
            "record_count": json.loads(norm_raw)["record_count"],
            "missing_count": sum(
                1 for r in json.loads(norm_raw)["records"] if r["value_decimal"] is None
            ),
            "rights_status": "private_review_pending",
            "manifest": {"key": f"{PREFIX}releases/{release_id}/manifest.json",
                         "sha256": hashlib.sha256(manifest_raw).hexdigest()},
            "normalized": {"key": f"{PREFIX}releases/{release_id}/normalized.json",
                           "sha256": hashlib.sha256(norm_raw).hexdigest()},
        }
        releases.append(entry)
        objects[entry["manifest"]["key"]] = Obj(manifest_raw)
        objects[entry["normalized"]["key"]] = Obj(norm_raw)
    index = {
        "version": "ddfi-benchmark-index-v1",
        "provider": "NYU Stern / Aswath Damodaran",
        "releases": releases,
        "rights_status": "private_review_pending",
        "guardrail": "third-party-derived reference estimates",
    }
    index_raw = json.dumps(index, sort_keys=True).encode()
    index_key = f"{PREFIX}index.json"
    objects[index_key] = Obj(index_raw)
    evidence = {
        "schema_version": "damodaran-publication-evidence-v1",
        "releases": [evidence_entry(entry, objects) for entry in releases],
    }
    evidence_raw = json.dumps(evidence, sort_keys=True).encode()
    evidence_key = f"{PREFIX}publication_evidence.json"
    objects[evidence_key] = Obj(evidence_raw)
    env = SimpleNamespace(
        MARKET_DATA=Bucket(objects),
        DDFI_BENCHMARKS_INDEX_KEY=index_key,
        DDFI_BENCHMARKS_INDEX_SHA256=hashlib.sha256(index_raw).hexdigest(),
        DDFI_BENCHMARKS_EVIDENCE_KEY=evidence_key,
        DDFI_BENCHMARKS_EVIDENCE_SHA256=hashlib.sha256(evidence_raw).hexdigest(),
    )
    return env, {"jan": jan_id, "jul": jul_id, "country": country_id}


def evidence_entry(entry, objects, **overrides):
    """A reviewed-evidence catalog entry bound to this release's source bytes."""
    manifest = json.loads(objects[entry["manifest"]["key"]].raw)
    source_sha = manifest["files"]["source.bin"]
    item = {
        "release_id": entry["release_id"],
        "dataset_id": entry["dataset_id"],
        "region": entry["region"],
        "source_sha256": source_sha,
        "download_retrieved_at": manifest["lineage"]["retrieved_at"],
        "download_retrieved_at_precision": "day",
        "download_retrieved_at_source": "manifest.lineage.retrieved_at",
        "published_at": entry["published_at"],
        "publication_evidence": {
            "method": "SERVER_LAST_MODIFIED",
            "source_url": "https://example.invalid/stern/workbook.xls",
            "server_last_modified": manifest["lineage"]["server_last_modified"],
            "observed_at": "2026-10-14T20:50:21+00:00",
            "statement": "synthetic header bound to the exact source bytes",
            "evidence_sha256": source_sha,
        },
        "historical_availability": "AVAILABLE_FROM_PUBLISHED_AT",
    }
    item.update(overrides)
    return item


def rewrite_evidence(env, mutate, *, clear_cache=True):
    """Rewrite the catalog object and repin its digest, as a curator would."""
    key = env.DDFI_BENCHMARKS_EVIDENCE_KEY
    catalog = json.loads(env.MARKET_DATA.objects[key].raw)
    mutate(catalog)
    raw = json.dumps(catalog, sort_keys=True).encode()
    env.MARKET_DATA.objects[key] = Obj(raw)
    env.DDFI_BENCHMARKS_EVIDENCE_SHA256 = hashlib.sha256(raw).hexdigest()
    if clear_cache:
        ddfi_benchmarks_api._METADATA_CACHE.clear()


def call(env, request, request_id="req-1"):
    try:
        return asyncio.run(query_existing(env, request, request_id=request_id))
    except PrivateFinancialsError as error:
        return {"error": {"code": error.code, "message": error.message, "status": error.status}}


def run(request):
    env, ids = make_env()
    return call(env, request), ids


def test_operations_advertised_and_dispatched():
    assert "benchmark_discovery" in OPERATIONS
    assert "benchmark_context" in OPERATIONS
    result, _ids = run({"operation": "benchmark_discovery"})
    assert result["schema_version"] == "zion-private-query-v1"
    assert result["operation"] == "benchmark_discovery"
    assert result["coverage"]["live_provider_calls"] == 0


def test_discovery_lists_releases_with_rights():
    result, ids = run({"operation": "benchmark_discovery"})
    datasets = result["data"]["datasets"]
    assert {d["dataset_id"] for d in datasets} == {"cost_of_capital", "country_risk_premiums"}
    cost = [d for d in datasets if d["dataset_id"] == "cost_of_capital"]
    assert {d["published_at"] for d in cost} == {"2026-01-12", "2026-06-01"}
    for item in datasets:
        assert item["rights_status"] == "private_review_pending"
        assert item["measures"]
        assert item["serving_release_id"].startswith("ddfi-benchmarks:")


def test_discovery_subjects_and_definitions():
    result, _ids = run({
        "operation": "benchmark_discovery", "dataset_id": "cost_of_capital",
        "include_subjects": True,
    })
    item = result["data"]["datasets"][0]
    assert "Software (System & Application)" in item["subjects"]
    assert item["definitions"]["wacc"]
    assert item["unit_definitions"]


def test_industry_context_carries_provenance_units_and_definitions():
    env, ids = make_env()
    result = call(env, {
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "measures": ["wacc", "tax_rate"],
        "expected_release_id": ids["jul"],
    })
    assert result["data"]["subject"] == "Software (System & Application)"
    by_measure = {o["measure"]: o for o in result["data"]["observations"]}
    assert by_measure["wacc"]["value_decimal"] == "0.0955"
    assert by_measure["wacc"]["unit"] == "fraction"
    assert by_measure["wacc"]["classification"] == "BENCHMARK_ASSUMPTION_NOT_COMPANY_FACT"
    assert by_measure["wacc"]["source_sheet"] == "Industry Averages"
    release = result["release"]
    assert release["ddfi_release_id"] == ids["jul"]
    assert release["selection_mode"] == "CURRENT_EXPLICIT"
    assert release["provider"] == "NYU Stern / Aswath Damodaran"
    assert release["source_url"]
    assert release["source_sha256"] and release["normalized_sha256"]
    assert release["ingested_bundle"] == "DAMODARAN_TEST_BUNDLE"
    assert release["rights"]["redistribution_policy"] == "PROHIBITED_PENDING_REVIEW"
    assert result["data"]["definitions"]["wacc"]
    assert result["data"]["unit_definitions"]["fraction"]
    assert result["data"]["measurement_period_note"]
    assert result["coverage"]["source_bytes_served"] is False
    assert result["coverage"]["evidence_path"]
    assert "wacc" in json.dumps(result) and "0.09344048072400105" not in json.dumps(result)


def test_country_context_serves_country_subject():
    env, ids = make_env()
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["country"],
        "subject": "France", "measures": ["country_risk_premium"],
    })
    observation = result["data"]["observations"][0]
    assert observation["value_decimal"] == "0.0061"
    assert observation["subject_type"] == "country"
    assert observation["subject_region"] == "Europe"
    assert result["data"]["region"] == "Global"


def test_exact_release_selection_returns_january_vintage():
    env, ids = make_env()
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "measures": ["wacc"],
    })
    assert result["release"]["ddfi_release_id"] == ids["jan"]
    assert result["release"]["selection_mode"] == "EXACT_PINNED"
    assert result["data"]["observations"][0]["value_decimal"] == "0.09344048072400105"


def test_historical_as_of_selects_eligible_release_not_newest():
    result, _ids = run({
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "measures": ["wacc"],
        "as_of": "2026-03-01",
    })
    # Newest release (2026-06-01) must NOT be served at a March cutoff.
    assert result["release"]["published_at"] == "2026-01-12"
    assert result["release"]["selection_mode"] == "HISTORICAL_AS_OF"
    assert result["coverage"]["as_of"] == "2026-03-01"
    assert result["data"]["observations"][0]["value_decimal"] == "0.09344048072400105"


def test_unavailable_historical_coverage_fails_closed():
    result, _ids = run({
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "measures": ["wacc"],
        "as_of": "2025-12-31",
    })
    assert result["error"]["code"] == "HISTORICAL_COVERAGE_UNAVAILABLE"
    assert result["error"]["status"] == 404
    assert "2026-01-12" in result["error"]["message"]
    assert "fallback is forbidden" in result["error"]["message"]


def test_future_release_request_rejected():
    env, ids = make_env()
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jul"],
        "subject": "Software (System & Application)", "measures": ["wacc"],
        "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_VINTAGE_UNAVAILABLE"
    assert ids["jul"][:12] in result["error"]["message"] or "2026-06-01" in result["error"]["message"]


def test_future_as_of_rejected():
    far = (datetime.now(timezone.utc).date().replace(year=datetime.now(timezone.utc).year + 1)).isoformat()
    result, _ids = run({
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "as_of": far,
    })
    assert result["error"]["code"] == "INVALID_REQUEST"


def test_expected_release_mismatch_is_409():
    env, ids = make_env()
    result = call(env, {
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "measures": ["wacc"],
        "expected_release_id": ids["jan"],
    })
    # Current selection is the July release -> caller pin must fail closed.
    assert result["error"]["code"] == "RELEASE_MISMATCH"
    assert result["error"]["status"] == 409


def test_missing_observation_is_explicit_not_dropped():
    env, ids = make_env()
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Bank (Money Center)", "measures": ["wacc"],
    })
    observation = result["data"]["observations"][0]
    assert observation["value_decimal"] is None
    assert observation["missing"] is True
    assert observation["quarantined"] is True
    assert observation["workbook_text"] == "NA"
    assert result["data"]["missing_count"] == 1
    assert result["coverage"]["status"] == "PARTIAL"


def test_unknown_measure_and_subject_fail_closed():
    result, _ids = run({
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "measures": ["not_a_measure"],
    })
    assert result["error"]["code"] == "BENCHMARK_MEASURE_UNKNOWN"
    result, _ids = run({
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "No Such Industry",
    })
    assert result["error"]["code"] == "BENCHMARK_SUBJECT_NOT_FOUND"


def test_bounded_validation_rejects_malformed_requests():
    cases = [
        {"operation": "benchmark_context", "dataset_id": "cost_of_capital"},
        {"operation": "benchmark_context", "dataset_id": "cost_of_capital",
         "subject": "x" * 200},
        {"operation": "benchmark_context", "dataset_id": "cost_of_capital",
         "subject": "Software (System & Application)", "measures": ["m%d" % i for i in range(21)]},
        {"operation": "benchmark_context", "dataset_id": "cost_of_capital",
         "subject": "Software (System & Application)", "include_source_bytes": True},
        {"operation": "benchmark_context", "dataset_id": "cost_of_capital", "release_id": "a" * 64,
         "subject": "Software (System & Application)"},
        {"operation": "benchmark_discovery", "dataset_id": "cost_of_capital", "include_subjects": 1},
    ]
    for request in cases:
        result, _ids = run(request)
        assert result["error"]["code"] == "INVALID_REQUEST", request


def test_tampered_release_bytes_fail_integrity():
    env, ids = make_env()
    entry = next(e for e in json.loads(env.MARKET_DATA.objects[env.DDFI_BENCHMARKS_INDEX_KEY].raw)["releases"]
                 if e["release_id"] == ids["jan"])
    raw = env.MARKET_DATA.objects[entry["normalized"]["key"]].raw
    tampered = raw.replace(b'"0.09344048072400105"', b'"0.9999999999999999"')
    env.MARKET_DATA.objects[entry["normalized"]["key"]] = Obj(tampered)
    with pytest.raises(PrivateFinancialsError) as excinfo:
        asyncio.run(query_existing(env, {
            "operation": "benchmark_context", "release_id": ids["jan"],
            "subject": "Software (System & Application)", "measures": ["wacc"],
        }, request_id="req-2"))
    assert excinfo.value.code == "BENCHMARK_INTEGRITY_FAILURE"


def test_unconfigured_index_fails_closed():
    env, _ids = make_env()
    env.DDFI_BENCHMARKS_INDEX_KEY = None
    ddfi_benchmarks_api._METADATA_CACHE.clear()
    with pytest.raises(PrivateFinancialsError) as excinfo:
        asyncio.run(query_existing(env, {"operation": "benchmark_discovery"}, request_id="req-3"))
    assert excinfo.value.code == "BENCHMARKS_NOT_CONFIGURED"


def test_response_never_contains_source_bytes():
    env, ids = make_env()
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)",
    })
    assert b"original workbook bytes" not in json.dumps(result).encode()
    assert result["release"]["rights"]["entitlement"] == "owner_private_reference"


def test_historical_selection_reports_reviewed_evidence():
    result, ids = run({
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "measures": ["wacc"],
        "as_of": "2026-03-01",
    })
    evidence = result["release"]["publication_evidence"]
    assert evidence["method"] == "SERVER_LAST_MODIFIED"
    assert evidence["download_precision"] == "day"
    assert evidence["catalog_sha256"]
    assert result["coverage"]["publication_evidence"] == (
        "REVIEWED_CATALOG_BOUND_TO_SERVED_SOURCE_BYTES")


def test_historical_without_configured_evidence_fails_closed():
    env, ids = make_env()
    env.DDFI_BENCHMARKS_EVIDENCE_KEY = None
    ddfi_benchmarks_api._METADATA_CACHE.clear()
    result = call(env, {
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARKS_EVIDENCE_NOT_CONFIGURED"
    assert result["error"]["status"] == 503


def test_current_selection_does_not_require_evidence_configuration():
    env, ids = make_env()
    env.DDFI_BENCHMARKS_EVIDENCE_KEY = None
    ddfi_benchmarks_api._METADATA_CACHE.clear()
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "measures": ["wacc"],
    })
    assert result["release"]["selection_mode"] == "EXACT_PINNED"
    assert "publication_evidence" not in result["release"]
    assert result["coverage"]["publication_evidence"] == "NOT_APPLICABLE_CURRENT_SELECTION"


def test_historical_release_without_an_evidence_entry_fails_closed():
    env, ids = make_env()
    rewrite_evidence(env, lambda catalog: catalog.__setitem__(
        "releases", [e for e in catalog["releases"] if e["release_id"] != ids["jan"]]))
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "measures": ["wacc"],
        "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_EVIDENCE_MISSING"
    assert result["error"]["status"] == 503


def test_evidence_catalog_digest_mismatch_fails_integrity():
    env, ids = make_env()
    env.DDFI_BENCHMARKS_EVIDENCE_SHA256 = "0" * 64
    ddfi_benchmarks_api._METADATA_CACHE.clear()
    result = call(env, {
        "operation": "benchmark_context", "dataset_id": "cost_of_capital",
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_INTEGRITY_FAILURE"
    assert result["error"]["status"] == 503


def test_evidence_bound_to_other_bytes_is_refused():
    env, ids = make_env()

    def mutate(catalog):
        entry = next(e for e in catalog["releases"] if e["release_id"] == ids["jan"])
        entry["source_sha256"] = "b" * 64

    rewrite_evidence(env, mutate)
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_EVIDENCE_MISMATCH"


def test_unreviewed_evidence_method_is_refused():
    env, ids = make_env()

    def mutate(catalog):
        entry = next(e for e in catalog["releases"] if e["release_id"] == ids["jan"])
        entry["publication_evidence"]["method"] = "GUESSED_VINTAGE_LABEL"

    rewrite_evidence(env, mutate)
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_EVIDENCE_MISMATCH"


def test_malformed_evidence_timestamp_is_refused():
    env, ids = make_env()

    def mutate(catalog):
        entry = next(e for e in catalog["releases"] if e["release_id"] == ids["jan"])
        entry["publication_evidence"]["observed_at"] = "2026-10-14T20:50:21"

    rewrite_evidence(env, mutate)
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_EVIDENCE_MISMATCH"


def test_evidence_disagreeing_with_manifest_publication_is_refused():
    env, ids = make_env()

    def mutate(catalog):
        entry = next(e for e in catalog["releases"] if e["release_id"] == ids["jan"])
        entry["published_at"] = "2026-01-13"

    rewrite_evidence(env, mutate)
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_EVIDENCE_MISMATCH"


def test_instant_retrieval_precision_is_reported_as_instant():
    env, ids = make_env()

    def mutate(catalog):
        entry = next(e for e in catalog["releases"] if e["release_id"] == ids["jan"])
        entry["download_retrieved_at"] = "2026-10-14T19:28:12+00:00"
        entry["download_retrieved_at_precision"] = "instant"

    rewrite_evidence(env, mutate)
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["release"]["publication_evidence"]["download_precision"] == "instant"


def test_warm_cache_does_not_bypass_evidence_validation():
    # A first historical call warms the release and index caches; a later
    # catalog change must still be validated against the served bytes.
    env, ids = make_env()
    first = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert first["release"]["ddfi_release_id"] == ids["jan"]

    def mutate(catalog):
        entry = next(e for e in catalog["releases"] if e["release_id"] == ids["jan"])
        entry["publication_evidence"]["server_last_modified"] = "Tue, 13 Jan 2026 00:00:00 GMT"

    # Keep the warmed index/release cache entries: the catalog digest is new, so
    # only the evidence object itself must be re-read and re-validated.
    rewrite_evidence(env, mutate, clear_cache=False)
    second = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert second["error"]["code"] == "BENCHMARK_EVIDENCE_MISMATCH"


def test_nested_evidence_hash_must_match_served_bytes():
    env, ids = make_env()

    def mutate(catalog):
        entry = next(e for e in catalog["releases"] if e["release_id"] == ids["jan"])
        # Outer binding is correct; the nested record names other bytes.
        entry["publication_evidence"]["evidence_sha256"] = "c" * 64

    rewrite_evidence(env, mutate)
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_EVIDENCE_MISMATCH"
    assert "different source bytes" in result["error"]["message"]


def test_evidence_dates_are_compared_in_utc():
    # 2026-01-11T23:30:00-02:00 is 2026-01-12T01:30Z: publication day in UTC.
    env, ids = make_env()
    rewrite_evidence(env, lambda catalog: next(
        e for e in catalog["releases"] if e["release_id"] == ids["jan"]
    )["publication_evidence"].__setitem__("observed_at", "2026-01-11T23:30:00-02:00"))
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result.get("release", {}).get("ddfi_release_id") == ids["jan"], result

    # 2026-01-12T01:30:00+03:00 is 2026-01-11T22:30Z: still before publication.
    env, ids = make_env()
    rewrite_evidence(env, lambda catalog: next(
        e for e in catalog["releases"] if e["release_id"] == ids["jan"]
    )["publication_evidence"].__setitem__("observed_at", "2026-01-12T01:30:00+03:00"))
    result = call(env, {
        "operation": "benchmark_context", "release_id": ids["jan"],
        "subject": "Software (System & Application)", "as_of": "2026-03-01",
    })
    assert result["error"]["code"] == "BENCHMARK_EVIDENCE_MISMATCH"
