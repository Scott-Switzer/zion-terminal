"""Verify owner data through a deployed origin; receipts never include private source values."""

import argparse
import hashlib
import json
from pathlib import Path

import sec_engine_acceptance as transport

PRODUCTION = "https://dcf-valuation-engine.scswitzer.workers.dev"
SOURCE = "https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", required=True, choices=[PRODUCTION, SOURCE])
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError("Output must be new")
    transport.BASE = args.base
    checks = {}
    metadata = {}

    def get(path, params=None, *, auth=True):
        return transport.request("/private/v1/" + path, params, auth=auth)

    def check(name, valid):
        checks[name] = bool(valid)
        if not valid:
            raise ValueError("Acceptance failed: " + name)

    _, m = get("stock-history", auth=False)
    check("owner_required", m["http_status"] == 401)
    first, m = get("stock-history", {"limit": 500})
    check("stock_catalog", m["http_status"] == 200)
    release = first["release_id"]
    symbols = first["symbols"]
    cursor = first["next_cursor"]
    while cursor:
        page, m = get("stock-history", dict(limit=500, cursor=cursor, expected_release_id=release))
        check("stock_catalog_page_" + str(len(symbols)), m["http_status"] == 200)
        symbols.extend(page["symbols"])
        cursor = page["next_cursor"]
    check(
        "all_stored_symbols", len(symbols) == 2629 and len({s["symbol"] for s in symbols}) == 2629
    )
    check("all_stored_row_declarations", sum(s["normalized_rows"] for s in symbols) == 856633)
    # Metadata chooses previously excluded symbols; actual provider rows never enter the receipt.
    selected = ["AAPL", "MSFT", symbols[0]["symbol"], symbols[-1]["symbol"]]
    cases = []
    for symbol in dict.fromkeys(selected):
        page, m = get("stock-history", dict(symbol=symbol, limit=3, expected_release_id=release))
        prices = page.get("prices", [])
        check("stock_" + symbol, m["http_status"] == 200 and bool(prices))
        check(
            "stock_" + symbol + "_source_qualification",
            all(
                r["available_at"] is None
                and r["price_basis"] == "RAW"
                and r["source_ids"]
                and r["close"] is not None
                for r in prices
            ),
        )
        check(
            "stock_" + symbol + "_lineage",
            all(sid in page["source_lineage"] for r in prices for sid in r["source_ids"]),
        )
        cases.append(
            dict(
                symbol=symbol,
                returned_rows=len(prices),
                source_pages=len(page["source_pages"]),
                source_read_bytes=page["source_read_bytes"],
                value_payload_sha256=hashlib.sha256(
                    json.dumps(prices, sort_keys=True).encode()
                ).hexdigest(),
            )
        )
    _, m = get("stock-history", dict(symbol="AAPL", as_of="2020-01-01"))
    check("no_false_price_pit", m["http_status"] == 422)
    _, m = get("stock-history", dict(expected_release_id="0" * 64))
    check("wrong_stock_release_refused", m["http_status"] == 409)
    history, m = get("historical-series", {"limit": 500})
    check("historical_catalog", m["http_status"] == 200 and len(history["series"]) == 147)
    check("missingness_windows_exposed", all("coverage" in row for row in history["series"]))
    metadata["historical_coverage_summary"] = history.get("coverage_summary")
    catalog, m = get("datasets/catalog", {"limit": 1})
    check(
        "existing_dataset_catalog",
        m["http_status"] == 200 and catalog.get("registered_releases") == 805,
    )
    stores, m = get("datasets")
    check(
        "all_four_private_stores", m["http_status"] == 200 and len(stores.get("sources", [])) == 4
    )
    for store in ("financial", "research", "filings", "lakehouse"):
        listing, m = get("datasets/" + store + "/objects", {"limit": 1})
        check(
            "private_store_" + store,
            m["http_status"] == 200 and (listing.get("objects") or listing.get("prefixes")),
        )
    receipt = dict(
        status="PASS",
        origin=args.base,
        role="PRODUCTION_DCF" if args.base == PRODUCTION else "SHARED_SOURCE",
        checks=checks,
        stock_release_id=release,
        stored_symbols=len(symbols),
        stored_normalized_rows=856633,
        stock_cases=cases,
        registered_dataset_releases=catalog.get("registered_releases"),
        private_stores=4,
        historical_series=147,
        metadata=metadata,
        private_values_printed=0,
        provider_requests=0,
        production_pointer_mutations=0,
    )
    args.output.write_text(json.dumps(receipt, indent=2))
    print(
        json.dumps(
            {k: v for k, v in receipt.items() if k not in ("stock_cases", "metadata")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
