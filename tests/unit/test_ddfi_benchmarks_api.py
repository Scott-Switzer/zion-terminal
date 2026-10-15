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
    env = SimpleNamespace(
        MARKET_DATA=Bucket(objects),
        DDFI_BENCHMARKS_INDEX_KEY=index_key,
        DDFI_BENCHMARKS_INDEX_SHA256=hashlib.sha256(index_raw).hexdigest(),
    )
    return env, {"jan": jan_id, "jul": jul_id, "country": country_id}


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
