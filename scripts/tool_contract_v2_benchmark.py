from __future__ import annotations

import collections
import http.client
import json
import os
import statistics
import time
from urllib.parse import urlsplit

WORLD = {"world_type": "real", "world_id": "us-public-markets"}
CASES = {
    "Q1": ("get_fundamentals", {"symbol": "MSFT", "metrics": ["revenue"], "period": "annual", "lookback": 1}),
    "Q2": ("get_fundamentals", {"symbol": "MSFT", "metrics": ["revenue", "operating_margin"], "period": "annual", "lookback": 1}),
    "Q3": ("get_fundamentals", {"symbol": "MSFT", "metrics": ["revenue"], "period": "quarterly", "lookback": 8}),
    "Q4": ("get_price_history", {"symbol": "TSLA", "limit": 50}),
    "Q5": ("compare", {"entities": ["MSFT", "ORCL", "CRM"], "metric": "operating_margin", "period": "annual", "lookback": 1}),
    "resolve_entity": ("resolve_entity", {"symbol": "MSFT"}),
    "resolve_security": ("resolve_security", {"symbol": "BRK.B"}),
    "revision_history": ("get_revision_history", {"symbol": "MSFT", "metric": "revenue", "period_type": "annual"}),
    "screen": ("screen", {"filters": [{"field": "revenue", "operator": "gt", "value": "0"}], "limit": 2}),
}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def run_case(base_url: str, case: str, samples: int) -> dict:
    tool, arguments = CASES[case]
    target = urlsplit(base_url)
    connection = http.client.HTTPSConnection(target.netloc, timeout=60)
    path = (target.path.rstrip("/") or "") + f"/v2/tools/{tool}"
    body = json.dumps({"world": WORLD, "arguments": arguments}).encode()
    rows = []
    try:
        for index in range(samples):
            started = time.perf_counter()
            try:
                connection.request("POST", path, body, {"content-type": "application/json", "connection": "keep-alive", "x-request-id": f"contract-v2-{case}-{index}"})
                response = connection.getresponse()
                raw = response.read()
                elapsed = (time.perf_counter() - started) * 1000
                document = json.loads(raw)
                rows.append({"ms": elapsed, "status": response.status, "release": document.get("release", {}).get("serving_release_id")})
            except Exception as error:
                rows.append({"ms": None, "status": type(error).__name__, "error": str(error)[:200]})
                connection.close()
                connection = http.client.HTTPSConnection(target.netloc, timeout=60)
    finally:
        connection.close()
    values = [row["ms"] for row in rows if row["ms"] is not None]
    result = {"case": case, "tool": tool, "samples": samples, "successful_samples": len(values), "statuses": dict(collections.Counter(str(row["status"]) for row in rows)), "p50_ms": round(statistics.median(values), 2) if values else None, "p90_ms": round(percentile(values, .90), 2) if values else None, "p95_ms": round(percentile(values, .95), 2) if values else None, "p99_ms": round(percentile(values, .99), 2) if values else None, "max_ms": round(max(values), 2) if values else None, "release_ids": sorted({row["release"] for row in rows if row.get("release")}), "errors": [row for row in rows if row["ms"] is None]}
    return result


def main() -> int:
    base_url = os.environ.get("ZION_BASE_URL", "https://api-thin-staging.scotttunnel.xyz")
    samples = int(os.environ.get("SAMPLES", "100"))
    cases = os.environ.get("CASES", ",".join(CASES)).split(",")
    results = []
    for case in cases:
        result = run_case(base_url, case, samples)
        print(json.dumps(result, sort_keys=True))
        results.append(result)
    if any(result["successful_samples"] != samples or set(result["statuses"]) != {"200"} for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
