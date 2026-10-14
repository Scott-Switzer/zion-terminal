"""Live private history acceptance; output contains no credentials or source row payloads."""
# ruff: noqa: E501

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def run(base, token, evidence=None):
    checks = []
    responses = {}

    def get(name, params=None, expected=200, authorized=True, method="GET"):
        query = urlencode(params or {})
        req = Request(
            base.rstrip("/") + "/private/v1/financial-history" + ("?" + query if query else ""),
            headers={
                "User-Agent": "zion-sec-history-acceptance/1",
                **({"Authorization": "Bearer " + token} if authorized else {}),
            },
            method=method,
        )
        start = time.perf_counter()
        try:
            with urlopen(req, timeout=35) as r:
                status, raw = r.status, r.read(2 * 1048576 + 1)
        except HTTPError as e:
            status, raw = e.code, e.read(2 * 1048576 + 1)
        data = json.loads(raw)
        checks.append(
            {
                "name": name,
                "status": "PASS" if status == expected else "FAIL",
                "http_status": status,
                "bytes": len(raw),
                "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                "error_code": data.get("error", {}).get("code"),
            }
        )
        return data

    ciks = ["0000320193", "0000909832", "0001045810", "0001326801", "0001652044"]
    metrics = "revenue,operating_income,net_income,operating_cash_flow,capital_expenditures,cash,total_debt,diluted_shares,free_cash_flow"
    get("unauthorized", {"cik": ciks[0]}, 401, False)
    get("writes_rejected", {"cik": ciks[0]}, 405, True, "POST")
    for cik in ciks:
        for freq in ["annual", "quarterly", "ttm"]:
            data = get(cik + ":" + freq, {"cik": cik, "metrics": metrics, "frequency": freq})
            responses[cik + ":" + freq] = data
            if evidence and "observations" in data:
                expected = json.loads(
                    (evidence / (cik + "." + freq + ".expected.json")).read_text()
                )
                match = (
                    data["observations"] == expected["observations"]
                    and data["coverage_gaps"] == expected["coverage_gaps"]
                )
                checks.append(
                    {
                        "name": cik + ":" + freq + ":independent_expected_match",
                        "status": "PASS" if match else "FAIL",
                        "observations": len(data["observations"]),
                        "coverage_gaps": len(data["coverage_gaps"]),
                    }
                )
                ids = {sid for row in data["observations"] for sid in row["source_observation_ids"]}
                checks.append(
                    {
                        "name": cik + ":" + freq + ":complete_lineage",
                        "status": "PASS" if ids == set(data["source_records"]) else "FAIL",
                    }
                )
    for name, params, status in [
        ("sql", {"cik": ciks[0], "sql": "SELECT 1"}, 400),
        ("identifier", {"cik": "AAPL"}, 400),
        ("licensed_dataset", {"cik": ciks[0], "dataset": "wrds"}, 400),
        ("unapproved_cik", {"cik": "0000000000"}, 403),
        ("invalid_metric", {"cik": ciks[0], "metrics": "liabilities_as_debt"}, 422),
        ("nonadditive_frequency", {"cik": ciks[0], "frequency": "weekly"}, 422),
        ("future_source_cutoff", {"cik": ciks[0], "as_of": "2026-10-13T00:00:00Z"}, 422),
        ("wrong_release", {"cik": ciks[0], "expected_release_id": "bad"}, 409),
        ("recipient", {"cik": ciks[0], "recipient": "llm"}, 403),
    ]:
        get(name, params, status)
    historical = get(
        "historical_availability",
        {
            "cik": ciks[0],
            "metrics": "revenue,operating_cash_flow",
            "frequency": "annual",
            "date_from": "2015-01-01",
            "date_to": "2020-12-31",
            "as_of": "2018-01-01T00:00:00Z",
        },
    )
    checks.append(
        {
            "name": "historical_no_future_records",
            "status": "PASS"
            if historical.get("observations")
            and all(r["available_at"] <= "2018-01-01T00:00:00Z" for r in historical["observations"])
            else "FAIL",
        }
    )
    first = responses.get(ciks[0] + ":annual", {})
    repeat = get(
        "immutable_repeat",
        {
            "cik": ciks[0],
            "metrics": metrics,
            "frequency": "annual",
            "expected_release_id": first.get("provenance", {}).get("release_id", "bad"),
        },
    )
    checks.append(
        {"name": "immutable_repeat_identical", "status": "PASS" if repeat == first else "FAIL"}
    )
    report = {
        "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
        "checks": checks,
        "base_origin": base,
        "source_release_id": first.get("provenance", {}).get("release_id"),
        "observations_returned": sum(len(r.get("observations", [])) for r in responses.values()),
        "source_lineage_examples": [
            {
                k: first["observations"][0][k]
                for k in [
                    "metric",
                    "period_start",
                    "period_end",
                    "concept",
                    "source_observation_ids",
                    "available_at",
                ]
            }
        ]
        if first.get("observations")
        else [],
        "public_result_sha256": hashlib.sha256(
            json.dumps(first, sort_keys=True).encode()
        ).hexdigest(),
        "licensed_sources_used": False,
    }
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--evidence", type=Path)
    a = p.parse_args()
    result = run(
        os.environ["ZION_PRIVATE_BASE_URL"], os.environ["ZION_PRIVATE_API_TOKEN"], a.evidence
    )
    Path(a.output).write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks": len(result["checks"]),
                "failures": [x for x in result["checks"] if x["status"] == "FAIL"],
                "observations": result["observations_returned"],
            }
        )
    )
    raise SystemExit(result["status"] != "PASS")


if __name__ == "__main__":
    main()
