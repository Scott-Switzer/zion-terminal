"""Publish metadata-only, hash-pinned catalog from an existing verified WRDS inventory."""

import argparse
import configparser
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def build(inventory, inventory_sha, coverage_reference):
    release = sha(
        encode({"inventory": inventory_sha, "coverage": coverage_reference, "version": 1})
    )
    prefix = f"gold/collection-catalog/releases/{release}/"
    objects = {}
    entries = []
    for record in inventory["releases"]:
        identifier = sha(record["manifest_key"].encode())[:32]
        detail = {
            "schema_version": "zion-native-dataset-v1",
            "id": identifier,
            "dataset": record["dataset"],
            "release": record["release"],
            "source": record["source"],
            "store": "research",
            "family": record["source_library"],
            "columns": record["columns"],
            "objects": record["objects"],
            "rows_declared": record.get("manifest_rows"),
            "manifest": {"key": record["manifest_key"], "sha256": record["manifest_sha256"]},
            "quality": "SOURCE_UNVALIDATED",
            "PIT_status": record.get("PIT_status", "NOT_ESTABLISHED"),
            "object_hash_status": "SOURCE_MANIFEST_DECLARED_NOT_REHASHED",
            "schema_status": "SOURCE_MANIFEST_DECLARED; USE_PARQUET_SCHEMA_ROUTE_FOR_FOOTER",
        }
        raw = encode(detail)
        if len(raw) > 1048576:
            raise ValueError("detail exceeds metadata budget")
        key = prefix + identifier + ".json"
        objects[key] = raw
        entries.append(
            {
                "id": identifier,
                "dataset": detail["dataset"],
                "release": detail["release"],
                "source": detail["source"],
                "store": "research",
                "family": detail["family"],
                "columns": len(detail["columns"]),
                "objects": len(detail["objects"]),
                "rows_declared": detail["rows_declared"],
                "reference": {"key": key, "sha256": sha(raw)},
            }
        )
    if len({e["id"] for e in entries}) != len(entries):
        raise ValueError("duplicate release identity")
    root = {
        "schema_version": "zion-native-catalog-v1",
        "release_id": release,
        "status": "PARTIAL",
        "registered_releases": len(entries),
        "unique_datasets": len({e["dataset"] for e in entries if e["dataset"]}),
        "object_references": sum(e["objects"] for e in entries),
        "entries": sorted(entries, key=lambda x: (x["dataset"] or "", x["release"] or "")),
        "scope": "EXISTING_WRDS_INVENTORY; OTHER_STORES_DISCOVERABLE_THROUGH_OBJECT_LISTING",
        "inventory": {
            "store": "research",
            "sha256": inventory_sha,
            "generated_at": inventory["generated_at"],
            "table_prefixes_declared": inventory.get("table_prefixes"),
            "recorded_error_count": len(inventory.get("errors", [])),
        },
        "cross_domain_coverage": coverage_reference,
        "acquisition_enabled": False,
        "quality": "SOURCE_UNVALIDATED",
        "schema_validation": "SOURCE_DECLARED_NOT_ALL_FOOTERS_SCANNED",
    }
    raw = encode(root)
    key = prefix + "manifest.json"
    objects[key] = raw
    if len(raw) > 1048576:
        raise ValueError("root exceeds metadata budget")
    return objects, {
        "key": key,
        "sha256": sha(raw),
        "release_id": release,
        "registered_releases": len(entries),
        "unique_datasets": root["unique_datasets"],
        "object_references": root["object_references"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    raw = args.inventory.read_bytes()
    if sha(raw) != args.inventory_sha256:
        raise ValueError("inventory digest mismatch")
    reference = {
        "store": "financial",
        "key": (
            "control/full-financial-v1/report/releases/"
            "6bd3ec154854fc027c4f0f96a68c0b43890cf765b51095ce8f472119361fdd20.json"
        ),
        "sha256": "6bd3ec154854fc027c4f0f96a68c0b43890cf765b51095ce8f472119361fdd20",
    }
    objects, metadata = build(json.loads(raw), args.inventory_sha256, reference)
    if args.publish:
        import boto3
        from botocore.config import Config

        config = configparser.ConfigParser(interpolation=None)
        config.read(Path.home() / ".config/rclone/rclone.conf")
        c = config["r2"]
        s = boto3.client(
            "s3",
            endpoint_url=c["endpoint"],
            aws_access_key_id=c["access_key_id"],
            aws_secret_access_key=c["secret_access_key"],
            region_name="auto",
            config=Config(max_pool_connections=4, retries={"max_attempts": 3}),
        )

        def put(item):
            key, body = item
            try:
                s.put_object(
                    Bucket="financial-system-datasets",
                    Key=key,
                    Body=body,
                    ContentType="application/json",
                    IfNoneMatch="*",
                )
            except s.exceptions.ClientError as e:
                if e.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                    raise
            stored = s.get_object(Bucket="financial-system-datasets", Key=key)["Body"].read(1048577)
            if stored != body:
                raise ValueError("catalog readback mismatch")

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(put, [(k, v) for k, v in objects.items() if k != metadata["key"]]))
        put((metadata["key"], objects[metadata["key"]]))
    args.output_metadata.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
