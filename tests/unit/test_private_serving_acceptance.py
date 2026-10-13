import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).parents[2] / "scripts/private_serving_acceptance.py"
spec = importlib.util.spec_from_file_location("private_acceptance", PATH)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def transport(*, mismatch=False, missing=False, auth_broken=False):
    def request(base, method, path, payload, token):
        if token != "secret-test-token":
            return (200 if auth_broken else 401), {}
        if method == "GET":
            return 200, {"operations": ["identity", "financials", "revenue_growth"]}
        if payload["operation"] == "revenue_growth" and missing:
            return 422, {
                "error": {"code": "COMPARABILITY_NOT_ESTABLISHED", "message": "secret-test-token"}
            }
        return 200, {
            "schema_version": "zion-private-query-v1",
            "entity": {"entity_id": "issuer", "symbol": payload["symbol"]},
            "release": {
                "serving_release_id": "r2"
                if mismatch and payload["operation"] == "financials"
                else "r1"
            },
            "data": {
                "observations": [
                    {
                        "metric": "revenue",
                        "value": "100",
                        "unit": "USD",
                        "period_type": "annual",
                        "period_end": "2024-12-31",
                        "evidence_id": "e",
                        "available_at": "2025-01-01",
                    }
                ],
                "year_over_year": [{"percent": "10"}] * 5,
            },
            "coverage": {"missing_reasons": []},
        }

    return request


def test_acceptance_requires_auth_and_consistent_release():
    report = acceptance.run_acceptance(
        "https://example.test", "secret-test-token", request_fn=transport()
    )
    assert report["status"] == "PASS"
    assert report["authenticated_api_verified"] is True
    assert "secret-test-token" not in json.dumps(report)
    report = acceptance.run_acceptance(
        "https://example.test", "secret-test-token", request_fn=transport(mismatch=True)
    )
    assert report["status"] == "FAIL"
    assert any(x["status"] == "FAIL" for x in report["checks"])


def test_missing_history_is_partial_not_pass_and_messages_not_logged():
    report = acceptance.run_acceptance(
        "https://example.test", "secret-test-token", request_fn=transport(missing=True)
    )
    assert report["status"] == "PARTIAL"
    assert "secret-test-token" not in json.dumps(report)


def test_auth_failure_cannot_be_hidden_by_other_successes():
    report = acceptance.run_acceptance(
        "https://example.test", "secret-test-token", request_fn=transport(auth_broken=True)
    )
    assert report["status"] == "FAIL"
    assert report["authenticated_api_verified"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test",
        "https://user:pass@example.test",
        "https://example.test?key=secret",
        "file:///tmp/a",
    ],
)
def test_unsafe_endpoints_rejected(url):
    with pytest.raises(ValueError):
        acceptance.run_acceptance(url, "secret-test-token", request_fn=transport())


def test_transport_exception_text_never_leaks():
    def broken(*args):
        raise RuntimeError("secret-test-token")

    report = acceptance.run_acceptance(
        "https://example.test", "secret-test-token", request_fn=broken
    )
    assert report["status"] == "FAIL"
    assert "secret-test-token" not in json.dumps(report)


@pytest.mark.parametrize(
    "bad",
    [{"release": None}, {"entity": {}}, {"data": {"observations": [{}], "year_over_year": None}}],
)
def test_malformed_success_is_not_acceptance(bad):
    underlying = transport()

    def request(*args):
        status, body = underlying(*args)
        if args[1] == "POST" and status == 200:
            body.update(bad)
        return status, body

    report = acceptance.run_acceptance(
        "https://example.test", "secret-test-token", request_fn=request
    )
    assert report["status"] == "FAIL"
