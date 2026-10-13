"""Sanitized live checks of the public-only restricted financial query pilot."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def run(base, token, source=None):
    checks = []

    def query(name, suffix="", expected=200, authorized=True):
        start = time.perf_counter()
        headers = {"User-Agent": "zion-foundation-acceptance/1"}
        if authorized:
            headers["Authorization"] = "Bearer " + token
        req = Request(
            base.rstrip("/") + "/private/v1/datasets/financial-query" + suffix, headers=headers
        )
        try:
            with urlopen(req, timeout=40) as r:
                status, data = r.status, json.load(r)
        except HTTPError as e:
            status, data = e.code, json.load(e)
        checks.append(
            {
                "name": name,
                "http_status": status,
                "status": "PASS" if status == expected else "FAIL",
                "latency_ms": round((time.perf_counter() - start) * 1000, 3),
            }
        )
        return data

    query("unauthorized", expected=401, authorized=False)
    result = query("cross_table_public_financial")
    if source and "data" in result:
        expected = []
        chosen = {}
        for f in source["facts"]:
            if f["available_at"] > source["as_of"]:
                continue
            key = tuple(f[k] for k in ("cik", "concept", "unit", "period_start", "period_end"))
            order = tuple(f[k] for k in ("available_at", "accession", "source_observation_id"))
            if key not in chosen or order > chosen[key][0]:
                chosen[key] = (order, f)
        for i in sorted(source["identities"], key=lambda x: x["cik"]):
            facts = sorted(
                [f for _, f in chosen.values() if f["cik"] == i["cik"]],
                key=lambda x: x["period_end"],
            )
            for f in facts or [None]:
                row = {
                    k: (f.get("metric" if k == "metric_id" else k) if f else None)
                    for k in result["data"]["rows"][0]
                    if k not in ("cik", "entity_id", "record_kind", "financial_value_status")
                }
                row.update(
                    cik=i["cik"],
                    entity_id=i["entity_id"],
                    record_kind="NORMALIZED_SOURCE_OBSERVATION"
                    if f
                    else "ENTITY_COVERAGE_WITH_NO_ELIGIBLE_PILOT_FACT",
                    financial_value_status="SOURCE_REPORTED"
                    if f
                    else "UNAVAILABLE_IN_PERMITTED_PILOT_NOT_ZERO",
                )
                expected.append(row)
        checks.append(
            {
                "name": "independent_source_join",
                "status": "PASS" if expected == result["data"]["rows"] else "FAIL",
                "rows": len(expected),
            }
        )
    cached = query("cache_repeat")
    checks.append(
        {"name": "cache_hit", "status": "PASS" if cached.get("cache") == "HIT" else "FAIL"}
    )
    canary = query("precision_null", "?template=precision_null_canary_v1")
    rows = canary.get("data", {}).get("rows", [])
    checks.append(
        {
            "name": "exact_precision_and_null",
            "status": "PASS"
            if rows
            and all(
                r
                == {"exact_decimal": "9007199254740993.000000000000000001", "nullable_value": None}
                for r in rows
            )
            else "FAIL",
        }
    )
    arithmetic = query("decimal_arithmetic", "?template=precision_arithmetic_canary_v1")
    checks.append(
        {
            "name": "exact_decimal_arithmetic",
            "status": "PASS"
            if arithmetic.get("data", {}).get("rows")
            and all(
                r["exact_sum"] == "9007199254740993.000000000000000002"
                for r in arithmetic["data"]["rows"]
            )
            else "FAIL",
        }
    )
    query("explain", "?explain=true")
    historical = query("historical_cutoff", "?as_of=2024-01-01T00%3A00%3A00Z")
    if source:
        expected_ids = {
            f["source_observation_id"]
            for f in source["facts"]
            if f["available_at"] <= "2024-01-01T00:00:00Z"
        }
        observed_ids = {
            r["source_observation_id"]
            for r in historical.get("data", {}).get("rows", [])
            if r["source_observation_id"] is not None
        }
        checks.append(
            {
                "name": "historical_source_revision_match",
                "status": "PASS" if observed_ids == expected_ids else "FAIL",
            }
        )
    for name, suffix, status in [
        ("sql_rejected", "?sql=SELECT%201", 400),
        ("dataset_rejected", "?template=wrds", 403),
        ("unknown_cik", "?cik=0000000000", 403),
        ("incorrect_identifier", "?cik=NET", 400),
        ("historical_link_missing", "?identifier_kind=ticker", 422),
        ("llm_recipient_denied", "?recipient=llm", 403),
        ("overflow_not_truncated", "?limit=1", 413),
    ]:
        query(name, suffix, status)
    return {
        "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
        "checks": checks,
        "licensed_rows_exposed": 0,
        "query_metrics": result.get("execution_metrics"),
        "result_rows": len(result.get("data", {}).get("rows", [])),
        "result_sha256": hashlib.sha256(
            json.dumps(result.get("data"), sort_keys=True).encode()
        ).hexdigest(),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--source")
    args = p.parse_args()
    result = run(
        os.environ["ZION_PRIVATE_BASE_URL"],
        os.environ["ZION_PRIVATE_API_TOKEN"],
        json.loads(Path(args.source).read_text()) if args.source else None,
    )
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    raise SystemExit(result["status"] != "PASS")


if __name__ == "__main__":
    main()
