"""Bounded live expansion checks; report contains no financial values or secrets."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from private_serving_acceptance import request_http


def run(base, token, *, request_fn=request_http):
    checks = []

    def execute(name, payload):
        started = time.perf_counter()
        try:
            status, body = request_fn(base, "POST", "/private/v1/query", payload, token)
        except Exception:
            status, body = 0, {}
        check = {
            "name": name,
            "http_status": status,
            "latency_ms": round(1000 * (time.perf_counter() - started), 2),
            "status": "FAIL",
        }
        checks.append(check)
        return check, body

    check, coverage = execute("coverage", {"operation": "sec_coverage"})
    counts = coverage.get("data", {})
    check["status"] = (
        "PASS"
        if check["http_status"] == 200
        and counts.get("indexed_filers", 0) >= 19000
        and counts.get("mapped_unique_issuers", 0) >= 6000
        else "FAIL"
    )
    for symbol in ("UBER", "ABNB", "PLTR", "NET", "COIN", "DDOG"):
        payload = {
            "operation": "sec_financials",
            "symbol": symbol,
            "metrics": ["revenue", "net_income", "total_assets", "operating_cash_flow"],
            "period": "annual",
            "lookback": 5,
        }
        cik = None
        for attempt in range(2):
            check, body = execute(f"{symbol}:{attempt}", payload)
            observations = body.get("data", {}).get("observations", [])
            valid = (
                bool(observations)
                and any(o.get("metric") == "revenue" for o in observations)
                and all(
                    o.get("concept", "").startswith("us-gaap:")
                    and o.get("unit") == "USD"
                    and o.get("period_end")
                    and o.get("available_at")
                    for o in observations
                )
            )
            valid = valid and bool(body.get("release", {}).get("sec_archive_release_id"))
            check.update(
                status="PASS"
                if check["http_status"] == 200
                and valid
                and (attempt == 0 or body.get("cache") == "HIT")
                else "FAIL",
                observation_count=len(observations),
                cache=body.get("cache"),
            )
            cik = body.get("entity", {}).get("cik")
        check, body = execute(
            f"{symbol}:historical",
            {
                "operation": "sec_financials",
                "cik": cik,
                "as_of": "2023-01-01T00:00:00Z",
                "metrics": ["revenue"],
            },
        )
        obs = body.get("data", {}).get("observations", [])
        check["status"] = (
            "PASS"
            if check["http_status"] == 200
            and obs
            and all(o.get("available_at", "9999") <= "2023-01-01T00:00:00Z" for o in obs)
            else "FAIL"
        )
    check, body = execute(
        "historical_symbol_rejected",
        {"operation": "sec_financials", "symbol": "UBER", "as_of": "2023-01-01T00:00:00Z"},
    )
    check["status"] = (
        "PASS"
        if check["http_status"] == 422
        and body.get("error", {}).get("code") == "DATED_IDENTITY_NOT_ESTABLISHED"
        else "FAIL"
    )
    return {
        "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
        **{
            k: counts.get(k)
            for k in ("indexed_filers", "mapped_unique_issuers", "mapped_ticker_symbols")
        },
        "checks": checks,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run(os.environ["ZION_PRIVATE_BASE_URL"], os.environ["ZION_PRIVATE_API_TOKEN"])
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if report["status"] == "PASS" else 1)
