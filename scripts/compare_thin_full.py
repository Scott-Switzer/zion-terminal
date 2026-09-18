from __future__ import annotations

import http.client
import json
import sys
from urllib.parse import urlsplit

FULL = "https://zion-financial-serving-v2-staging.scswitzer.workers.dev"
THIN = "https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev"
WORLD = {"world_type": "real", "world_id": "us-public-markets"}
CASES = [
    ("q1_revenue", "/v1/query", {"query": "What is AAPL revenue?", "world": WORLD}),
    ("q2_multi_metric", "/v1/query", {"query": "What are AAPL revenue and operating margin?", "world": WORLD}),
    ("q3_quarters", "/v1/query", {"query": "Show AAPL quarterly revenue history", "world": WORLD}),
    ("q4_prices", "/v1/query", {"query": "Show AAPL price history", "world": WORLD}),
    ("q5_compare", "/v1/query", {"query": "Compare AAPL MSFT NVDA operating margin", "world": WORLD}),
    ("pit_canary", "/v1/query", {"query": "What is AAPL revenue?", "world": WORLD, "as_of": "2025-11-01T00:00:00Z"}),
    ("unknown_entity", "/v1/query", {"query": "What is ZZZZ revenue?", "world": WORLD}),
    ("invalid_world", "/v1/query", {"query": "What is AAPL revenue?", "world": {"world_type": "real", "world_id": "unknown"}}),
]


def fetch(base: str, path: str, body: dict) -> tuple[int, dict]:
    target = urlsplit(base)
    connection = http.client.HTTPSConnection(target.netloc, timeout=30)
    try:
        connection.request("POST", target.path.rstrip("/") + path, json.dumps(body).encode(), {"content-type": "application/json"})
        response = connection.getresponse()
        raw = response.read()
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            document = {"error": {"http_body": raw.decode(errors="replace")}}
        return response.status, document
    except Exception as error:
        return 599, {"error": {"client_exception": type(error).__name__, "message": str(error)}}
    finally:
        connection.close()


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in sorted(value.items()) if key not in {"request_id", "answer", "serving_telemetry", "retrieved_at", "temporal_schema_version", "temporal_contract_hash", "temporal_contract_sha256"}}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def main() -> int:
    mismatches = []
    for name, path, body in CASES:
        full_status, full_body = fetch(FULL, path, body)
        thin_status, thin_body = fetch(THIN, path, body)
        full_norm = normalize(full_body)
        thin_norm = normalize(thin_body)
        same = full_status == thin_status and full_norm == thin_norm
        print(json.dumps({"case": name, "full_status": full_status, "thin_status": thin_status, "same": same, "full_release": full_body.get("release", {}).get("serving_release_id"), "thin_release": thin_body.get("release", {}).get("serving_release_id")}, sort_keys=True))
        if not same:
            mismatches.append({"case": name, "full": {"status": full_status, "body": full_norm}, "thin": {"status": thin_status, "body": thin_norm}})
    if mismatches:
        print(json.dumps({"unexplained_mismatches": len(mismatches), "mismatches": mismatches}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"comparison_count": len(CASES), "unexplained_mismatches": 0, "status": "PASS"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
