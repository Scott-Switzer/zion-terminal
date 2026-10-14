"""Bounded same-route SEC baseline/after benchmark; never prints financial values."""

import argparse
import hashlib
import json
import math
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

RELEASE = "b39a670bad0670449256e59d38bf6b9600503449baded29c25e41d5fb368c6bb"
PARAMS = {
    "metrics": "revenue,net_income,operating_cash_flow",
    "frequency": "annual",
    "date_from": "2015-01-01",
    "date_to": "2026-09-24",
    "as_of": "2026-09-24T00:00:00Z",
    "limit": 1000,
    "expected_release_id": RELEASE,
}
PATH = "/private/v1/financial-history"
MAX_RESPONSE = 2 * 1024**2


def encode(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(obj):
    return hashlib.sha256(encode(obj)).hexdigest()


def projection(data):
    """Duplicate preserving exact financial rows, gaps and public source lineage."""
    return {key: data.get(key) for key in ("observations", "coverage_gaps", "source_records")}


def percentile(values, probability):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * probability) - 1)] if ordered else None


def summarize(rows):
    values = [r["latency_ms"] for r in rows]
    return {
        "requests": len(rows),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "response_bytes": sum(r["response_bytes"] for r in rows),
        "cache_states": dict(Counter(r["cache_state"] for r in rows)),
    }


def protected(path, raw):
    with path.open("xb") as f:
        path.chmod(0o600)
        f.write(raw)


def run(args):
    from sec_engine_acceptance import credential

    parsed = urlsplit(args.base)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query:
        raise ValueError("CREDENTIAL_FREE_HTTPS_ORIGIN_REQUIRED")
    if parsed.path not in ("", "/"):
        raise ValueError("ORIGIN_REQUIRED")
    cohort_raw = args.cohort.read_bytes()
    ciks = [r["cik"] for r in json.loads(cohort_raw)["companies"]]
    if (
        len(ciks) != 20
        or len(set(ciks)) != 20
        or any(len(cik) != 10 or not cik.isdigit() for cik in ciks)
    ):
        raise ValueError("EXACT_20_COHORT_REQUIRED")
    shape = {"cohort": ciks, "params": PARAMS, "path": PATH, "concurrency": 3, "passes": 2}
    if args.phase == "after":
        before = json.loads((args.out / "before/receipt.json").read_bytes())
        if before["shape_sha256"] != digest(shape) or before["status"] != "PASS":
            raise ValueError("BASELINE_QUERY_COHORT_MISMATCH")
    phase_dir = args.out / args.phase
    phase_dir.mkdir(parents=True, exist_ok=False)
    phase_dir.chmod(0o700)
    token = credential()

    def fetch(task):
        pass_number, cik = task
        url = args.base.rstrip("/") + PATH + "?" + urlencode({**PARAMS, "cik": cik})
        started = time.perf_counter()
        try:
            with urlopen(
                Request(
                    url,
                    headers={
                        "Authorization": "Bearer " + token,
                        "User-Agent": "Zion-Matched-SEC-Benchmark/1.0",
                        "Accept-Encoding": "identity",
                    },
                ),
                timeout=40,
            ) as response:
                status, raw = response.status, response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            status, raw = error.code, error.read(MAX_RESPONSE + 1)
        duration = round((time.perf_counter() - started) * 1000, 3)
        if len(raw) > MAX_RESPONSE:
            raise ValueError("RESPONSE_BUDGET")
        filename = f"pass{pass_number}-{cik}.json"
        protected(phase_dir / filename, raw)
        data = json.loads(raw)
        provenance = data.get("provenance", {})
        release = provenance.get("release_id") or data.get("release_id")
        pagination = data.get("pagination", {})
        complete = not pagination.get("next_cursor") and not data.get("next_cursor")
        cache = data.get("cache", {})
        return {
            "pass": pass_number,
            "cik": cik,
            "http_status": status,
            "latency_ms": duration,
            "response_bytes": len(raw),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "response_file": filename,
            "projection_sha256": digest(projection(data)),
            "observation_count": len(data.get("observations", [])),
            "source_release_id": release,
            "source_manifest_sha256": provenance.get("manifest_sha256"),
            "registry_sha256": provenance.get("registry_sha256"),
            "cache_state": cache.get("state", "UNINSTRUMENTED"),
            "source_payload_bytes": cache.get("source_payload_bytes"),
            "archive_bytes_read_interactively": cache.get("archive_bytes_read_interactively"),
            "r2_read_count": None,
            "r2_read_count_reason": "API_DOES_NOT_EXPOSE_MEASURED_READ_COUNT",
            "complete": complete,
            "valid": status == 200 and release == RELEASE and complete,
        }

    rows = []
    for pass_number in (1, 2):
        with ThreadPoolExecutor(max_workers=3) as pool:
            current = list(pool.map(fetch, [(pass_number, cik) for cik in ciks]))
        rows.extend(current)
        protected(phase_dir / f"pass{pass_number}-measurements.json", encode(current))
        print(
            json.dumps({"phase": args.phase, "pass": pass_number, **summarize(current)}), flush=True
        )
        if not all(row["valid"] for row in current):
            raise ValueError("STATUS_RELEASE_OR_PAGINATION_MISMATCH")
    stable = all(
        rows[i]["projection_sha256"] == rows[i + 20]["projection_sha256"] for i in range(20)
    )
    receipt = {
        "phase": args.phase,
        "status": "PASS" if stable else "FAIL_REPEAT_PARITY",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "deployment_label": args.deployment_label,
        "shape": shape,
        "shape_sha256": digest(shape),
        "cohort_source_sha256": hashlib.sha256(cohort_raw).hexdigest(),
        "requests": rows,
        "summaries": {
            "first_observed": summarize(rows[:20]),
            "repeat": summarize(rows[20:]),
            **{
                state: summarize([r for r in rows if r["cache_state"] == state])
                for state in sorted({r["cache_state"] for r in rows})
            },
        },
        "percentile_method": "NEAREST_RANK",
        "repeat_exact_projection_parity": stable,
        "provider_requests": 0,
        "sql_requests": 0,
        "max_http_requests": 40,
        "cloud_writes": "EXISTING_API_QUERY_CACHE_ONLY",
        "cold_claim": False,
    }
    if args.phase == "after":
        parity = all(
            a["projection_sha256"] == b["projection_sha256"]
            for a, b in zip(before["requests"], rows)
        )
        receipt["baseline_exact_projection_parity"] = parity
        receipt["baseline_receipt_sha256"] = hashlib.sha256(
            (args.out / "before/receipt.json").read_bytes()
        ).hexdigest()
        if not parity:
            receipt["status"] = "FAIL_BASELINE_PARITY"
    protected(phase_dir / "receipt.json", encode(receipt))
    print(json.dumps({"phase": args.phase, "status": receipt["status"], "requests": len(rows)}))
    return receipt["status"] == "PASS"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("before", "after"), required=True)
    p.add_argument("--cohort", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--deployment-label", required=True)
    p.add_argument(
        "--base", default="https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev"
    )
    args = p.parse_args()
    raise SystemExit(0 if run(args) else 1)


if __name__ == "__main__":
    main()
