import importlib.util
from pathlib import Path

PATH = Path(__file__).parents[2] / "scripts/benchmark_evidence_acceptance.py"
spec = importlib.util.spec_from_file_location("benchmark_acceptance", PATH)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)

TOKEN = "secret-test-token"
BASE = "https://benchmark.example"


def context(dataset_id, *, catalog=None, evidence=True, release_id=None, mode="HISTORICAL_AS_OF"):
    expected_id, published_at, measure, _subject = acceptance.EXPECTED[dataset_id]
    release = {
        "ddfi_release_id": release_id or expected_id,
        "dataset_id": dataset_id,
        "published_at": published_at,
        "selection_mode": mode,
        "source_sha256": "a" * 64,
    }
    coverage = {
        "as_of": acceptance.COST_OF_CAPITAL_CUTOFF if mode == "HISTORICAL_AS_OF" else None,
        "source_sha256": "a" * 64,
        "publication_evidence": (
            acceptance.EVIDENCE_STATE if evidence else acceptance.CURRENT_EVIDENCE_STATE
        ),
    }
    if evidence:
        release["publication_evidence"] = {
            "method": "SERVER_LAST_MODIFIED",
            "observed_at": "2026-01-13T00:00:00+00:00",
            "download_precision": "day",
            "catalog_sha256": catalog or acceptance.CATALOG_SHA256,
        }
    return {
        "schema_version": "zion-private-query-v1",
        "release": release,
        "coverage": coverage,
        "data": {
            "observations": [
                {
                    "measure": measure,
                    "value_decimal": "0.1",
                    "classification": "BENCHMARK_ASSUMPTION_NOT_COMPANY_FACT",
                }
            ]
        },
    }


def transport(*, catalog=None, evidence=True, release_id=None):
    def request(base, method, path, payload, token):
        if token != TOKEN:
            return 401, {"error": {"code": "UNAUTHORIZED"}}
        if payload.get("as_of") == "2026-01-11":
            code = (
                "BENCHMARK_VINTAGE_UNAVAILABLE"
                if "release_id" in payload
                else "HISTORICAL_COVERAGE_UNAVAILABLE"
            )
            return 404, {"error": {"code": code}}
        as_of = payload.get("as_of")
        if as_of and as_of > "2026-10-25":
            return 400, {"error": {"code": "INVALID_REQUEST"}}
        mode = "CURRENT_EXPLICIT" if "as_of" not in payload else "HISTORICAL_AS_OF"
        return 200, context(
            payload["dataset_id"],
            catalog=catalog,
            evidence=evidence and mode != "CURRENT_EXPLICIT",
            release_id=release_id,
            mode=mode,
        )

    return request


def test_acceptance_passes_with_bound_evidence():
    report = acceptance.run_acceptance(BASE, TOKEN, request_fn=transport())
    assert report["status"] == "PASS"
    assert report["evidence_catalog_sha256"] == acceptance.CATALOG_SHA256
    assert "cost_of_capital" in report["evidence_bound_datasets"]


def test_acceptance_fails_on_catalog_digest_mismatch():
    report = acceptance.run_acceptance(BASE, TOKEN, request_fn=transport(catalog="b" * 64))
    assert report["status"] == "FAIL"
    historical = [c for c in report["checks"] if c["name"] == "historical:cost_of_capital:2026-06-01"]
    assert historical and historical[0]["status"] == "FAIL"


def test_acceptance_fails_when_historical_evidence_is_absent():
    report = acceptance.run_acceptance(BASE, TOKEN, request_fn=transport(evidence=False))
    assert report["status"] == "FAIL"
    assert any(
        c["name"].startswith("historical:") and c["status"] == "FAIL" for c in report["checks"]
    )


def test_acceptance_fails_on_substituted_release():
    report = acceptance.run_acceptance(BASE, TOKEN, request_fn=transport(release_id="c" * 64))
    assert report["status"] == "FAIL"
    assert any(c["name"].startswith("historical:") and c["status"] == "FAIL" for c in report["checks"])


def test_token_never_appears_in_report():
    report = acceptance.run_acceptance(BASE, TOKEN, request_fn=transport())
    assert TOKEN not in repr(report)
