from __future__ import annotations

import collections
import http.client
import json
import os
import statistics
import time
from urllib.parse import urlsplit

CASES = {
    "Q1": "What is AAPL revenue?",
    "Q2": "What are AAPL revenue and operating margin?",
    "Q3": "Show AAPL quarterly revenue history",
    "Q4": "Show AAPL price history",
    "Q5": "Compare AAPL MSFT NVDA operating margin",
}
WORLD = {"world_type": "real", "world_id": "us-public-markets"}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> int:
    base_url = os.environ.get("BASE_URL", "https://api-thin-staging.scotttunnel.xyz")
    case = os.environ.get("QUERY_CASE", "Q1")
    samples = int(os.environ.get("SAMPLES", "100"))
    if case not in CASES:
        raise SystemExit(f"unsupported QUERY_CASE={case}")
    target = urlsplit(base_url)
    payload = json.dumps({"query": CASES[case], "world": WORLD}).encode()
    conn = http.client.HTTPSConnection(target.netloc, timeout=30)
    rows: list[dict] = []
    try:
        for index in range(samples):
            try:
                started = time.perf_counter()
                conn.request("POST", target.path.rstrip("/") + "/v1/query", payload, {
                    "content-type": "application/json",
                    "connection": "keep-alive",
                    "x-request-id": f"temporal-acceptance-{case}-{index}",
                })
                response = conn.getresponse()
                raw = response.read()
                elapsed = (time.perf_counter() - started) * 1000
                headers = {key.lower(): value for key, value in response.getheaders()}
                document = json.loads(raw)
                release = document.get("release", {}).get("serving_release_id") or document.get("world", {}).get("version")
                rows.append({"ms": elapsed, "status": response.status, "bytes": len(raw), "release": release, "cf_ray": headers.get("cf-ray")})
            except Exception as error:
                rows.append({"ms": None, "status": type(error).__name__, "error": str(error)[:200]})
                try:
                    conn.close()
                finally:
                    conn = http.client.HTTPSConnection(target.netloc, timeout=30)
    finally:
        conn.close()
    values = [row["ms"] for row in rows if row["ms"] is not None]
    result = {
        "case": case,
        "base_url": base_url,
        "samples": len(rows),
        "successful_samples": len(values),
        "statuses": dict(collections.Counter(str(row["status"]) for row in rows)),
        "p50_ms": round(statistics.median(values), 2) if values else None,
        "p90_ms": round(percentile(values, .90), 2) if values else None,
        "p95_ms": round(percentile(values, .95), 2) if values else None,
        "p99_ms": round(percentile(values, .99), 2) if values else None,
        "max_ms": round(max(values), 2) if values else None,
        "release_ids": sorted({row["release"] for row in rows if row.get("release")}),
        "errors": [row for row in rows if row["ms"] is None],
    }
    print(json.dumps(result, sort_keys=True))
    if len(values) != samples or any(row["status"] != 200 for row in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
