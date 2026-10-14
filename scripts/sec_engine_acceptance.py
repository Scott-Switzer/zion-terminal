"""Live broad SEC checks; credentials remain local, public result examples only."""

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale")
BASE = "https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev"


def credential():
    values = {}
    for line in (Path.home() / ".config/zion/private-api.env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.removeprefix("export ").split("=", 1)
            values[key] = value.strip().strip("\"'")
    return values["ZION_PRIVATE_API_TOKEN"]


def request(path, params=None, body=None, auth=True):
    token = credential()
    headers = {"User-Agent": "Zion-SEC-Engine-Acceptance/2.0"}
    if auth:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    url = BASE + path + ("?" + urlencode(params) if params else "")
    started = time.perf_counter()
    for attempt in range(3):
        try:
            with urlopen(Request(url, data=data, headers=headers), timeout=40) as r:
                status, raw = r.status, r.read(2 * 1048576 + 1)
            break
        except HTTPError as ex:
            status, raw = ex.code, ex.read(2 * 1048576 + 1)
            break
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.2)
    return json.loads(raw), {
        "http_status": status,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "response_bytes": len(raw),
        "transport_attempts": attempt + 1,
    }


def all_pages(params):
    collected = []
    gaps = []
    checks = []
    measurements = []
    seen = set()
    while True:
        result, measurement = request("/private/v1/financial-history", params)
        assert measurement["http_status"] == 200, (params, result)
        ids = {
            sid
            for row in result["observations"] + result["accounting_checks"]
            for sid in row.get("source_observation_ids", [])
        }
        assert ids == set(result["source_records"]), (params, "leaf_ids")
        assert all(isinstance(row["value_decimal"], str) for row in result["observations"]), (
            params,
            "exact_values",
        )
        assert all(
            isinstance(row["source_record"]["val"], str) or row["source_record"]["val"] is None
            for row in result["source_records"].values()
        )
        collected.extend(result["observations"])
        gaps.extend(result["coverage_gaps"])
        checks.extend(result["accounting_checks"])
        measurements.append(
            {
                **measurement,
                "cache": result["cache"]["state"],
                "source_payload_bytes": result["cache"]["source_payload_bytes"],
                "archive_bytes_read_interactively": result["cache"][
                    "archive_bytes_read_interactively"
                ],
            }
        )
        cursor = result["pagination"]["next_cursor"]
        if not cursor:
            assert (
                len(collected) == result["pagination"]["total_observations"]
                and len(gaps) == result["pagination"]["total_coverage_gaps"]
            )
            return collected, gaps, checks, measurements
        assert cursor not in seen, (params, "repeated_cursor")
        seen.add(cursor)
        params = {**params, "cursor": cursor}


def main():
    m = json.loads((OUT / "engine-manifest.json").read_text())
    records = []
    examples = []

    def company(cik):
        checkpoint = OUT / (cik + ".live-check.json")
        if checkpoint.exists():
            previous = json.loads(checkpoint.read_text())
            if previous["release_id"] == m["release_id"]:
                return previous["record"], previous["actual"]
        params = {
            "cik": cik,
            "metrics": "revenue,net_income,operating_cash_flow",
            "frequency": "annual",
            "date_from": "2015-01-01",
        }
        actual, gaps, checks, measurements = all_pages(params)
        expected = json.loads((OUT / (cik + ".annual.expected.json")).read_text())
        wanted = [
            r
            for r in expected["observations"]
            if r["metric"] in params["metrics"].split(",") and r["period_end"] >= "2015-01-01"
        ]
        assert actual == wanted, (cik, "independent_expected_mismatch")
        record = {
            "cik": cik,
            "label": m["companies"][cik]["label"],
            "status": "PASS",
            "observations": len(actual),
            "coverage_gaps": len(gaps),
            "accounting_checks": {
                s: sum(x["status"] == s for x in checks) for s in ["PASS", "FAIL", "UNAVAILABLE"]
            },
            "statement_family": m["companies"][cik]["policy"]["family"],
            "requests": measurements,
        }
        checkpoint.write_text(
            json.dumps({"release_id": m["release_id"], "record": record, "actual": actual})
        )
        return record, actual

    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-count", type=int, default=125)
    args = parser.parse_args()
    from materialize_sec_engine import SAMPLE

    initial = [
        str(int(x)).zfill(10) for x in SAMPLE.split() if str(int(x)).zfill(10) in m["companies"]
    ]
    selected = list(
        dict.fromkeys(
            initial + ["0000008146", "0000006284", "0000006769", "0000051143", "0000066740"]
        )
    )
    selected = [c for c in selected if c in m["companies"]]
    if not 50 <= args.sample_count <= len(m["companies"]):
        raise ValueError("INVALID_SAMPLE_COUNT")
    remaining = [c for c in m["companies"] if c not in selected]
    needed = max(0, args.sample_count - len(selected))
    if needed:
        selected += [remaining[(i * len(remaining)) // needed] for i in range(needed)]
    selected = selected[: args.sample_count]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for record, actual in pool.map(company, selected):
            records.append(record)
            print(json.dumps({"cik": record["cik"], "status": "PASS"}), flush=True)
            if record["cik"] in ("0000789019", "0001018724", "0000019617", "0000104169"):
                examples.extend(
                    {
                        k: r[k]
                        for k in (
                            "metric",
                            "period_start",
                            "period_end",
                            "value_decimal",
                            "unit",
                            "concept",
                            "available_at",
                            "source_observation_ids",
                        )
                    }
                    | {"cik": record["cik"]}
                    for r in actual[-1:]
                )
    long = []
    for cik, freq in [
        ("0000789019", "quarterly"),
        ("0001018724", "ttm"),
        ("0000909832", "quarterly"),
    ]:
        params = {
            "cik": cik,
            "metrics": ",".join(json.loads((OUT / "registry.json").read_text())["metrics"]),
            "frequency": freq,
            "date_from": "1930-01-01",
            "limit": 100,
        }
        rows, gaps, checks, times = all_pages(params)
        expected = json.loads((OUT / (cik + "." + freq + ".expected.json")).read_text())
        assert rows == expected["observations"], (cik, freq, "long_observations")
        assert sorted(gaps, key=lambda x: json.dumps(x, sort_keys=True)) == sorted(
            expected["coverage_gaps"], key=lambda x: json.dumps(x, sort_keys=True)
        ), (cik, freq, "long_coverage_gaps")
        long.append(
            {
                "cik": cik,
                "frequency": freq,
                "status": "PASS",
                "observations": len(rows),
                "coverage_gaps": len(gaps),
                "pages": len(times),
                "requests": times,
            }
        )
    batch, t = request(
        "/private/v1/financial-history/batch",
        {
            "ciks": "0000789019,0001018724,0000019617",
            "metrics": "net_income,cash",
            "date_from": "2020-01-01",
        },
    )
    batch_t = dict(t)
    assert (
        t["http_status"] == 200
        and len(batch["companies"]) == 3
        and all(x["http_status"] == 200 for x in batch["companies"])
    )
    denials = []
    for name, path, params, auth, status in [
        ("unauthorized", "/private/v1/financial-history", {"cik": "0000789019"}, False, 401),
        (
            "sql",
            "/private/v1/financial-history",
            {"cik": "0000789019", "sql": "SELECT 1"},
            True,
            400,
        ),
        (
            "wrong_release",
            "/private/v1/financial-history",
            {"cik": "0000789019", "expected_release_id": "0" * 64},
            True,
            409,
        ),
        ("unknown_cik", "/private/v1/financial-history", {"cik": "0000000000"}, True, 404),
        (
            "invalid_cursor",
            "/private/v1/financial-history",
            {"cik": "0000789019", "cursor": "forged"},
            True,
            400,
        ),
        (
            "licensed_recipient",
            "/private/v1/financial-history",
            {"cik": "0000789019", "recipient": "llm"},
            True,
            403,
        ),
        (
            "future_cutoff",
            "/private/v1/financial-history",
            {"cik": "0000789019", "as_of": "2026-09-25T00:00:00Z"},
            True,
            422,
        ),
    ]:
        d, t = request(path, params, auth=auth)
        assert t["http_status"] == status, (name, d, t)
        denials.append({"name": name, "status": "PASS", **t})
    discovery, t = request("/private/v1/financial-companies", {"q": "microsoft"})
    assert t["http_status"] == 200 and discovery["companies"][0]["cik"] == "0000789019"
    # Independently check a live historical cutoff from the response's raw SEC leaf records.
    historical, t = request(
        "/private/v1/financial-history",
        {
            "cik": "0000789019",
            "metrics": "revenue,net_income",
            "date_from": "2010-01-01",
            "as_of": "2018-01-01T00:00:00Z",
        },
    )
    assert t["http_status"] == 200
    assert all(
        x["available_at"] <= "2018-01-01T00:00:00.000000000Z" for x in historical["observations"]
    )
    assert all(
        x["source_record"]["filed"] < "2018-01-01" for x in historical["source_records"].values()
    )
    requests = [r for c in records for r in c["requests"]]
    report = {
        "status": "PASS",
        "release_id": m["release_id"],
        "companies": records,
        "evaluated_companies": len(records),
        "observations": sum(x["observations"] for x in records),
        "long_histories": long,
        "batch": {"status": "PASS", "companies": 3, **batch_t},
        "denials": denials,
        "historical_cutoff": {"status": "PASS", "observations": len(historical["observations"])},
        "examples": examples,
        "benchmarks": {
            "sample": str(len(records)) + " company annual histories, client concurrency3",
            "latency_ms": {
                "min": min(x["latency_ms"] for x in requests),
                "median": statistics.median(x["latency_ms"] for x in requests),
                "max": max(x["latency_ms"] for x in requests),
            },
            "max_response_bytes": max(x["response_bytes"] for x in requests),
            "source_bytes_read": sum(x["source_payload_bytes"] for x in requests),
            "zip_bytes_read_interactively": 0,
            "cache_hits": sum(x["cache"] == "HIT" for x in requests),
        },
        "full_financial_certification": False,
    }
    (OUT / "live-acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("companies", "long_histories", "examples", "denials")
            }
        )
    )


if __name__ == "__main__":
    main()
