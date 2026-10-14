"""Portable private SEC API smoke checks; reports no credentials or source row payloads."""

import argparse
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    base = os.environ["ZION_PRIVATE_BASE_URL"].rstrip("/")
    token = os.environ["ZION_PRIVATE_API_TOKEN"]
    checks = []

    def get(name, path, params, expected=200, authorized=True):
        headers = {"User-Agent": "Zion-SEC-Engine-Smoke/1"}
        if authorized:
            headers["Authorization"] = "Bearer " + token
        request = Request(base + path + "?" + urlencode(params), headers=headers)
        try:
            with build_opener(NoRedirect()).open(request, timeout=35) as r:
                status, raw = r.status, r.read(2 * 1048576 + 1)
        except HTTPError as ex:
            status, raw = ex.code, ex.read(2 * 1048576 + 1)
        assert len(raw) <= 2 * 1048576
        data = json.loads(raw)
        checks.append(
            {
                "name": name,
                "http_status": status,
                "status": "PASS" if status == expected else "FAIL",
            }
        )
        return data

    get("unauthorized", "/private/v1/financial-history", {"cik": "0000789019"}, 401, False)
    first = None
    for cik in ["0000789019", "0001018724", "0000019617"]:
        value = get(
            "history:" + cik,
            "/private/v1/financial-history",
            {"cik": cik, "metrics": "net_income,operating_cash_flow,cash", "limit": 10},
        )
        assert value["observations"] and all(
            isinstance(x["value_decimal"], str) for x in value["observations"]
        )
        assert all(
            sid in value["source_records"]
            for row in value["observations"]
            for sid in row["source_observation_ids"]
        )
        assert value["provenance"]["licensed_sources_used"] is False
        if first is None:
            first = value
    assert first["pagination"]["next_cursor"]
    second = get(
        "signed_pagination",
        "/private/v1/financial-history",
        {
            "cik": "0000789019",
            "metrics": "net_income,operating_cash_flow,cash",
            "limit": 10,
            "cursor": first["pagination"]["next_cursor"],
            "expected_release_id": first["provenance"]["release_id"],
        },
    )
    assert second["pagination"]["offset"] == 10
    get(
        "wrong_release",
        "/private/v1/financial-history",
        {"cik": "0000789019", "expected_release_id": "0" * 64},
        409,
    )
    get(
        "sql_denied", "/private/v1/financial-history", {"cik": "0000789019", "sql": "SELECT 1"}, 400
    )
    get("screen_threshold_budget", "/private/v1/financial-screen", {"minimum": "1e999999999"}, 422)
    screen = get(
        "screen",
        "/private/v1/financial-screen",
        {"metric": "operating_cash_flow", "minimum": "0", "limit": 3},
    )
    assert screen["matches"] and screen["release_id"] == first["provenance"]["release_id"]
    companies = get("discovery", "/private/v1/financial-companies", {"q": "Microsoft"})
    assert companies["companies"][0]["cik"] == "0000789019"
    result = {
        "status": "PASS" if all(x["status"] == "PASS" for x in checks) else "FAIL",
        "release_id": first["provenance"]["release_id"],
        "checks": checks,
    }
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result))
    raise SystemExit(result["status"] != "PASS")


if __name__ == "__main__":
    main()
