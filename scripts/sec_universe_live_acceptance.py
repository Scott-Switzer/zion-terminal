"""Bounded live SEC reconciliation against separately hash-read native source payloads."""

import argparse
import json
import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from pathlib import Path

from reconcile_financial_inventory import client
from sec_engine_acceptance import request
from sec_publish_update_release import read


def verify_rows(response, data, release):
    assert response["provenance"]["release_id"] == release
    assert response["provenance"]["licensed_sources_used"] is False
    sources = {row["source_observation_id"]: row for row in data["observations"]}
    for sid, leaf in response.get("source_records", {}).items():
        source = sources[sid]
        assert leaf["value_decimal"] == source["value_decimal"]
        assert leaf["source_record"]["val"] == source["value_decimal"]
        for key in (
            "concept",
            "unit",
            "currency",
            "period_start",
            "period_end",
            "available_at",
            "accession",
        ):
            assert leaf.get(key) == source.get(key)
    for observation in response["observations"]:
        assert isinstance(observation["value_decimal"], str)
        with localcontext() as context:
            context.prec = 200
            value = Decimal(0)
            for term in observation["source_terms"]:
                source = sources[term["source_observation_id"]]
                assert source["available_at"] <= response["as_of"]
                assert source["unit"] == observation["unit"]
                assert source["currency"] == observation["currency"]
                assert source["value_decimal"] is not None
                value += Decimal(source["value_decimal"]) * term["coefficient"]
        assert value == Decimal(observation["value_decimal"])
    return len(response["observations"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    args = parser.parse_args()
    assert 1 <= args.count <= 100
    publication = json.loads(args.publication.read_text())
    s = client()
    root = json.loads(read(s, publication["root"], 1048576))
    directory = json.loads(read(s, root["directory"], 4 * 1048576))
    ciks = sorted(row["cik"] for row in directory if row["status"] == "MATERIALIZED")
    chosen = list(
        dict.fromkeys(
            ciks[i * (len(ciks) - 1) // max(args.count - 1, 1)] for i in range(args.count)
        )
    )
    measurements = []

    def check(cik):
        shard = json.loads(read(s, root["shards"][cik[-2:]], 16 * 1048576))
        entry = shard[cik]
        data = json.loads(read(s, entry["materialized"], 16 * 1048576))
        q = {
            "cik": cik,
            "metrics": "revenue,net_income,operating_cash_flow",
            "frequency": "annual",
            "date_from": "2015-01-01",
            "limit": 1000,
            "expected_release_id": root["release_id"],
        }
        response, measurement = request("/private/v1/financial-history", q)
        assert measurement["http_status"] == 200, (cik, measurement)
        assert response["pagination"]["next_cursor"] is None, "BOUNDED_SAMPLE_REQUIRES_MORE_PAGES"
        observations = verify_rows(response, data, root["release_id"])
        return {
            "cik": cik,
            "status": "PASS",
            "observations": observations,
            "coverage_gaps": len(response["coverage_gaps"]),
            "source_payload_bytes": entry["materialized"]["bytes"],
            **measurement,
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        measurements = list(pool.map(check, chosen))
    latency = sorted(row["latency_ms"] for row in measurements)
    result = {
        "status": "PASS",
        "release_id": root["release_id"],
        "companies": len(measurements),
        "observations": sum(row["observations"] for row in measurements),
        "latency_ms": {
            "p50": statistics.median(latency),
            "p95": latency[max(0, math.ceil(0.95 * len(latency)) - 1)],
            "max": max(latency),
        },
        "source_payload_bytes": sum(row["source_payload_bytes"] for row in measurements),
        "api_response_bytes": sum(row["response_bytes"] for row in measurements),
        "archive_bytes_read_interactively": 0,
        "client_concurrency": 3,
        "checks": measurements,
        "qualification": (
            "ONE_BOUNDED_RUN;EXACT_SOURCE_TERMS_AND_LEAF_RECONCILIATION;"
            "NOT_FULL_FINANCIAL_CERTIFICATION"
        ),
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}))


if __name__ == "__main__":
    main()
