"""Read-only production CRSP/daily acceptance; no provider price values in receipts."""

import argparse
import hashlib
import json
from pathlib import Path

import sec_engine_acceptance as t

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", type=Path, required=True)
args = p.parse_args()
if args.output.exists():
    raise ValueError("Output must be new")
t.BASE = "https://dcf-valuation-engine.scswitzer.workers.dev"
checks = {}


def check(name, value):
    checks[name] = bool(value)
    if not value:
        raise ValueError("Acceptance failed: " + name)


def get(params=None, auth=True):
    return t.request("/private/v1/stock-history", params, auth=auth)


_, m = get(auth=False)
check("owner_auth", m["http_status"] == 401)
v, m = get({"limit": 500})
check("crsp_default", m["http_status"] == 200 and v.get("source") == "WRDS_CRSP")
release = v["release_id"]
catalog = v["symbols"]
cursor = v["next_cursor"]
pages = 1
while cursor:
    v, m = get(dict(limit=500, cursor=cursor, expected_release_id=release))
    check("catalog_page_" + str(pages), m["http_status"] == 200)
    catalog.extend(v["symbols"])
    cursor = v["next_cursor"]
    pages += 1
check(
    "all_native_security_ids",
    len(catalog) == 40518 and len({x["permno"] for x in catalog}) == 40518,
)
check("all_stored_crsp_rows", sum(x["normalized_rows"] for x in catalog) == 110245749)
old = next(
    x
    for x in catalog
    if x.get("date_max") and x["date_max"] < "2000-01-01" and x["normalized_rows"] > 100
)
cases = []
for permno, start, end in [
    (14593, "2025-01-01", "2025-12-31"),
    (old["permno"], old["date_max"][:4] + "-01-01", old["date_max"]),
    (catalog[-1]["permno"], catalog[-1]["date_min"], catalog[-1]["date_max"]),
]:
    q = dict(permno=permno, date_from=start, date_to=end, limit=3, expected_release_id=release)
    v, m = get(q)
    rows = v.get("prices", [])
    check("prices_" + str(permno), m["http_status"] == 200 and bool(rows))
    check(
        "lineage_" + str(permno),
        all(
            r["security_id"] == "CRSP:PERMNO:" + str(permno)
            and r["available_at"] is None
            and r["open"] is None
            and r["adjusted_close"] is None
            and all(s in v["source_lineage"] for s in r["source_ids"])
            for r in rows
        ),
    )
    if v.get("next_cursor"):
        q["cursor"] = v["next_cursor"]
        n, nm = get(q)
        check("price_pagination_" + str(permno), nm["http_status"] == 200 and bool(n.get("prices")))
    cases.append(
        dict(
            permno=permno,
            returned_rows=len(rows),
            source_pages=len(v["source_pages"]),
            value_payload_sha256=hashlib.sha256(
                json.dumps(rows, sort_keys=True).encode()
            ).hexdigest(),
        )
    )
_, m = get({"permno": 14593, "as_of": "2020-01-01"})
check("no_fake_price_pit", m["http_status"] == 422)
v, m = get({"source": "alpaca_latest", "limit": 500})
check("latest_daily", m["http_status"] == 200 and v["coverage"]["session"] == "2026-10-19")
latest_count = v["total_matches"]
latest_release = v["release_id"]
check("published_daily_universe", latest_count == 11497)
v, m = get({"source": "alpaca_latest", "symbol": "AAPL"})
check(
    "latest_actual_price",
    m["http_status"] == 200
    and len(v.get("prices", [])) == 1
    and v["prices"][0]["close"] is not None,
)
v, m = get({"source": "alpaca", "limit": 1})
check("older_alpaca_archive_preserved", m["http_status"] == 200 and v["symbol_count"] == 2629)
receipt = dict(
    status="PASS",
    origin=t.BASE,
    checks=checks,
    crsp_release_id=release,
    catalog_pages=pages,
    securities=len(catalog),
    indexed_rows=110245749,
    crsp_price_cases=cases,
    latest_daily_rows=latest_count,
    latest_daily_release=latest_release,
    provider_requests=0,
    private_values_printed=0,
    production_pointer_mutations=0,
)
args.output.write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps({k: v for k, v in receipt.items() if k != "checks"}))
