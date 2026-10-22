"""Index every existing Alpaca normalized source, retaining original price files in place.

No provider acquisition, duplicate price publication, automatic CIK join or CURRENT mutation.
Only safe aggregate progress is printed. Raw private caches remain outside Git, mode0600.
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/cloudflare-query/src"))
from prepare_owner_market_release import currency_contract
from reconcile_financial_inventory import client
from stock_archive import build, digest, encode, parse_bars

BUCKET = "financial-system-datasets"


def index_inventory(inventory_path, directory, s3, *, publish=False):
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    cache = directory / "source-cache"
    cache.mkdir(exist_ok=True)
    cache.chmod(0o700)
    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw)
    datasets = inventory["alpaca"]["datasets"]
    if len(datasets) > 1000:
        raise ValueError("SOURCE_DATASET_BUDGET")
    pages = {}
    index = {}
    physical = 0
    network_reads = 0
    cache_reads = 0

    def read(key, expected=None, maximum=8 * 1048576):
        nonlocal network_reads, cache_reads
        if (
            not key.startswith(
                ("normalized/alpaca/source-raw-daily-v1/", "raw/alpaca/equity-bars/")
            )
            or ".." in key
            or "\\" in key
        ):
            raise ValueError("SOURCE_PATH")
        location = cache / (digest(key.encode()) + ".bin")
        if location.exists():
            raw = location.read_bytes()
            if len(raw) <= maximum and (expected is None or digest(raw) == expected):
                cache_reads += 1
                return raw
        response = s3.get_object(Bucket=BUCKET, Key=key)
        raw = response["Body"].read(maximum + 1)
        if (
            len(raw) > maximum
            or len(raw) != response["ContentLength"]
            or (expected is not None and digest(raw) != expected)
        ):
            raise ValueError("SOURCE_INTEGRITY")
        location.write_bytes(raw)
        location.chmod(0o600)
        network_reads += 1
        return raw

    for dataset in datasets:
        if dataset["bytes"] > 8 * 1048576:
            raise ValueError("NORMALIZED_OBJECT_BUDGET")
        raw = read(dataset["normalized_key"], dataset["sha256"])
        if len(raw) != dataset["bytes"]:
            raise ValueError("NORMALIZED_SIZE")
        file = pq.ParquetFile(io.BytesIO(raw))
        if file.metadata.num_rows != dataset["normalized_rows"]:
            raise ValueError("NORMALIZED_COUNT")
        columns = [
            "provider_symbol",
            "session_date",
            "source_object_key",
            "source_hash",
            "feed",
            "price_basis",
            "adjustment_method",
            "alpaca_asset_uuid",
        ]
        for batch in file.iter_batches(batch_size=20000, columns=columns):
            physical += batch.num_rows
            for row in batch.to_pylist():
                if row["feed"] != "SIP":
                    continue
                symbol = row["provider_symbol"]
                if (
                    not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,24}", symbol)
                    or row["price_basis"] != "RAW"
                    or row["adjustment_method"] != "NONE"
                ):
                    raise ValueError("NORMALIZED_SOURCE_CONTRACT")
                sha = row["source_hash"]
                key = row["source_object_key"]
                day = str(row["session_date"])
                page = pages.setdefault(
                    sha, dict(key=key, sha256=sha, symbols={}, normalized_rows=0)
                )
                if page["key"] != key:
                    raise ValueError("SOURCE_HASH_PATH_CONFLICT")
                page["symbols"][symbol] = page["symbols"].get(symbol, 0) + 1
                page["normalized_rows"] += 1
                entry = index.setdefault(
                    symbol,
                    dict(
                        source_pages=set(),
                        normalized_rows=0,
                        date_min=day,
                        date_max=day,
                        asset_ids=set(),
                    ),
                )
                entry["source_pages"].add(sha)
                entry["normalized_rows"] += 1
                entry["date_min"] = min(entry["date_min"], day)
                entry["date_max"] = max(entry["date_max"], day)
                if row.get("alpaca_asset_uuid"):
                    entry["asset_ids"].add(row["alpaca_asset_uuid"])
        print(
            json.dumps(
                dict(
                    phase="normalized_source_index",
                    datasets_read=len(pages),
                    rows=physical,
                    symbols=len(index),
                )
            ),
            flush=True,
        )
    invalid = 0
    native_rows = 0
    for i, (sha, page) in enumerate(sorted(pages.items())):
        raw = read(page["key"], sha)
        manifest_key = page["key"].removesuffix(".json") + ".manifest.json"
        manifest_raw = read(manifest_key, maximum=262144)
        manifest = json.loads(manifest_raw)
        if manifest.get("storage_key") != page["key"] or manifest.get("content_sha256") != sha:
            raise ValueError("RAW_REQUEST_MANIFEST")
        proof = currency_contract(manifest)
        # Parse each page once during validation; no price value is printed or republished.
        payload = json.loads(raw, parse_float=__import__("decimal").Decimal)
        actual = 0
        for symbol, expected_count in page["symbols"].items():
            bars = payload["bars"].get(symbol, [])
            if len(bars) != expected_count:
                raise ValueError("RAW_NORMALIZED_SYMBOL_COUNT")
            # Keep original Decimal tokens; parse each original page only once.
            # parse_page must receive original bytes, not a float reserialization.
            rows, gaps = parse_bars(bars)
            actual += len(rows) + len(gaps)
            invalid += len(gaps)
        if actual != page["normalized_rows"]:
            raise ValueError("RAW_NORMALIZED_PAGE_COUNT")
        native_rows += actual
        page.update(
            bytes=len(raw),
            currency=proof["currency"],
            currency_evidence=proof,
            request_manifest=dict(
                key=manifest_key, sha256=digest(manifest_raw), bytes=len(manifest_raw)
            ),
            feed="SIP",
            price_basis="RAW",
            adjustment_method="NONE",
        )
        page.pop("symbols")
        print(
            json.dumps(
                dict(
                    phase="original_source_verified",
                    pages=i + 1,
                    total_pages=len(pages),
                    original_rows=native_rows,
                    invalid_rows=invalid,
                )
            ),
            flush=True,
        )
    if invalid:
        raise ValueError("SOURCE_INVALID_ROWS_REQUIRE_QUARANTINE")
    for entry in index.values():
        entry["source_pages"] = sorted(entry["source_pages"])
        entry["asset_ids"] = sorted(entry["asset_ids"])
    objects, ref = build(
        index, pages, inventory_sha256=digest(inventory_raw), normalized_rows=physical
    )
    files = directory / "publication"
    files.mkdir(exist_ok=True)
    for key, raw in objects.items():
        local = files / key
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(raw)
    receipt = dict(
        status="BUILT",
        reference=ref,
        physical_normalized_rows=physical,
        original_rows_verified=native_rows,
        source_pages=len(pages),
        symbols=len(index),
        index_bytes=sum(map(len, objects.values())),
        provider_requests=0,
        duplicate_price_uploads=0,
        network_source_reads=network_reads,
        verified_cache_reads=cache_reads,
        publication_pointer_writes=0,
    )
    if publish:
        from concurrent.futures import ThreadPoolExecutor

        def put(item):
            key, raw = item
            try:
                s3.put_object(
                    Bucket=BUCKET,
                    Key=key,
                    Body=raw,
                    IfNoneMatch="*",
                    ContentType="application/json",
                )
            except Exception as error:
                if (
                    getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
                    != 412
                ):
                    raise
            stored = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read(1048577)
            if digest(stored) != digest(raw):
                raise ValueError("INDEX_READBACK")

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(put, [(k, v) for k, v in objects.items() if k != ref["key"]]))
        put((ref["key"], objects[ref["key"]]))
        receipt.update(status="PUBLISHED_VERIFIED", objects_readback_verified=len(objects))
    (directory / "receipt.json").write_bytes(encode(receipt))
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--publish", action="store_true")
    args = p.parse_args()
    try:
        print(
            json.dumps(
                index_inventory(args.inventory, args.directory, client(), publish=args.publish)
            )
        )
    except Exception as error:
        print(json.dumps(dict(status="FAILED", error_type=type(error).__name__)), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
