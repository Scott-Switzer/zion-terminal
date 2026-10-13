import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "expanded_acceptance", Path(__file__).parents[2] / "scripts/sec_expanded_acceptance.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_request(malformed=False):
    def request(base, method, path, payload, token):
        if payload["operation"] == "sec_coverage":
            return 200, {
                "data": {
                    "indexed_filers": 19863,
                    "mapped_unique_issuers": 6545,
                    "mapped_ticker_symbols": 8419,
                }
            }
        if payload.get("as_of") and payload.get("symbol"):
            return 422, {"error": {"code": "DATED_IDENTITY_NOT_ESTABLISHED"}}
        observations = [
            {
                "metric": "revenue",
                "concept": "us-gaap:Revenues",
                "unit": "USD",
                "value": "100",
                "period_end": "2021-12-31",
                "available_at": "2022-03-01T05:00:00Z",
            }
        ]
        return 200, {
            "schema_version": "zion-private-query-v1",
            "entity": {"cik": "0000001234"},
            "data": {"observations": [] if malformed else observations},
            "cache": "HIT",
            "release": {"sec_archive_release_id": "a" * 64},
            "coverage": {},
        }

    return request


def test_expansion_report_verifies_persistence_without_leaking_values_or_tokens():
    result = module.run("https://example.test", "secret-example", request_fn=fixture_request())
    assert result["status"] == "PASS"
    assert result["mapped_unique_issuers"] == 6545
    assert "secret-example" not in json.dumps(result)
    assert '"value"' not in json.dumps(result)


def test_empty_observations_never_count_as_expanded_financial_coverage():
    result = module.run(
        "https://example.test", "secret-example", request_fn=fixture_request(malformed=True)
    )
    assert result["status"] == "FAIL"
