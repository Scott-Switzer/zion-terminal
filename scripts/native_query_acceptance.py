"""Live native table checks; reports never include source rows or credentials."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from private_serving_acceptance import request_http


def run(base, token, *, request_fn=request_http):
    parsed = urlparse(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not token
    ):
        raise ValueError("credential-free HTTPS origin and token required")
    checks = []

    def execute(name, path, authorized=True):
        started = time.perf_counter()
        try:
            s, b = request_fn(base, "GET", path, None, token if authorized else "")
        except Exception:
            s, b = 0, {}
        c = {
            "name": name,
            "http_status": s,
            "latency_ms": round(1000 * (time.perf_counter() - started), 2),
            "status": "FAIL",
        }
        checks.append(c)
        return c, b

    c, b = execute("unauthorized_catalog", "/private/v1/datasets/catalog", False)
    c["status"] = "PASS" if c["http_status"] == 401 else "FAIL"
    c, root = execute("catalog", "/private/v1/datasets/catalog?limit=1")
    c["status"] = (
        "PASS"
        if c["http_status"] == 200
        and root.get("registered_releases", 0) >= 805
        and root.get("unique_datasets", 0) >= 803
        and root.get("object_references", 0) >= 41485
        else "FAIL"
    )
    for dataset, columns in [
        ("ff_all__factors_daily", ["date", "mktrf"]),
        ("block__block", ["ticker", "shpct"]),
        ("crsp__msf", ["permno", "date", "ret"]),
    ]:
        c, b = execute(
            dataset + ":lookup",
            "/private/v1/datasets/catalog?" + urlencode({"search": dataset, "limit": 20}),
        )
        entries = [e for e in b.get("entries", []) if e.get("dataset") == dataset]
        if c["http_status"] != 200 or not entries:
            continue
        c["status"] = "PASS"
        identifier = entries[0]["id"]
        c, detail = execute(dataset + ":detail", "/private/v1/datasets/catalog/" + identifier)
        objects = [o for o in detail.get("objects", []) if o.get("key", "").endswith(".parquet")]
        if c["http_status"] != 200 or not objects:
            continue
        c["status"] = "PASS"
        path = objects[0]["key"]
        store = detail["store"]
        c, schema = execute(
            dataset + ":schema", f"/private/v1/datasets/{store}/schema?" + urlencode({"path": path})
        )
        fields = schema.get("data", {}).get("columns", [])
        c["status"] = (
            "PASS"
            if c["http_status"] == 200 and set(columns) <= {f["name"] for f in fields}
            else "FAIL"
        )
        payload = {"path": path, "columns": ",".join(columns), "limit": 3}
        url = f"/private/v1/datasets/{store}/rows?" + urlencode(payload)
        cursor = None
        for attempt in range(2):
            c, b = execute(f"{dataset}:rows:{attempt}", url)
            rows = b.get("data", {}).get("rows", [])
            valid = (
                bool(rows)
                and all(set(row) == set(columns) for row in rows)
                and b.get("quality") == "SOURCE_UNVALIDATED"
            )
            c.update(
                status="PASS"
                if c["http_status"] == 200 and valid and (attempt == 0 or b.get("cache") == "HIT")
                else "FAIL",
                returned_rows=len(rows),
                cache=b.get("cache"),
            )
            cursor = b.get("next_cursor")
        c, b = execute(
            dataset + ":continuation", url + "&" + urlencode({"cursor": cursor or "invalid"})
        )
        c["status"] = (
            "PASS"
            if c["http_status"] == 200
            and b.get("data", {}).get("row_offset") == 3
            and b.get("data", {}).get("rows")
            else "FAIL"
        )
        bad = f"/private/v1/datasets/{store}/rows?" + urlencode(
            {**payload, "columns": ",".join(reversed(columns)), "cursor": cursor or "invalid"}
        )
        c, b = execute(dataset + ":cursor_scope", bad)
        c["status"] = (
            "PASS"
            if c["http_status"] == 400 and b.get("error", {}).get("code") == "CURSOR_SCOPE_MISMATCH"
            else "FAIL"
        )
    return {
        "status": "PASS"
        if len(checks) == 23 and all(c["status"] == "PASS" for c in checks)
        else "FAIL",
        "catalog_release_id": root.get("release_id"),
        "registered_releases": root.get("registered_releases"),
        "unique_datasets": root.get("unique_datasets"),
        "object_references": root.get("object_references"),
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
