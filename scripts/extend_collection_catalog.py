"""Register completed remote preservation manifests in the EXISTING native catalog.

Reads control/manifest metadata only; never downloads Parquet or advances CURRENT.
The output is a candidate immutable catalog, not normalized financial facts.
"""

import argparse
import configparser
import hashlib
import importlib.util
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "catalog_publisher", Path(__file__).with_name("publish_collection_catalog.py")
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
PRIVATE = "financial-data-wrds-private"
FINANCIAL = "financial-system-datasets"
STAGED = "control/wrds/preservation-staged/"
HEX = re.compile(r"[a-f0-9]{64}")


def remote_record(value, key, digest):
    """Normalize only the source manifest shape; values remain exact native text."""
    dataset, run = value.get("dataset_id", ""), value.get("run_id", "")
    if not re.fullmatch(r"[a-z0-9_]+", dataset) or not re.fullmatch(r"[A-Za-z0-9_-]+", run):
        raise ValueError("REMOTE_IDENTITY_INVALID")
    base = f"raw/wrds/{dataset}/releases/{run}/"
    if (
        key != base + "manifest.json"
        or not HEX.fullmatch(digest)
        or value.get("schema_version") != "wrds-remote-text-parquet-v1"
        or value.get("source") != "WRDS"
        or value.get("publication_state") != "STAGED"
        or value.get("canonical_ready") is not False
        or value.get("availability_time") is not None
        or value.get("PIT_status") != "NOT_ESTABLISHED"
        or value.get("encoding") != "POSTGRESQL_NATIVE_TEXT"
        or value.get("null_semantics") != "SQL_NULL_PRESERVED_AS_PARQUET_NULL"
    ):
        raise ValueError("REMOTE_MANIFEST_POLICY_INVALID")
    columns, parts = value.get("columns"), value.get("parts")
    if not isinstance(columns, list) or not columns or not isinstance(parts, list):
        raise ValueError("REMOTE_SCHEMA_INVALID")
    names = []
    for col in columns:
        if (
            not isinstance(col.get("name"), str)
            or not col["name"]
            or not isinstance(col.get("source_postgresql_type"), str)
            or col.get("storage_type") != "string"
        ):
            raise ValueError("REMOTE_COLUMN_INVALID")
        names.append(col["name"])
    if len(set(names)) != len(names):
        raise ValueError("REMOTE_DUPLICATE_COLUMN")
    for i, part in enumerate(parts):
        if (
            part.get("key") != base + f"part-{i:06d}.parquet"
            or not HEX.fullmatch(part.get("sha256", ""))
            or type(part.get("rows")) is not int
            or part["rows"] <= 0
            or type(part.get("bytes")) is not int
            or part["bytes"] <= 0
        ):
            raise ValueError("REMOTE_PART_INVALID")
    if type(value.get("row_count")) is not int or value["row_count"] != sum(
        p["rows"] for p in parts
    ):
        raise ValueError("REMOTE_ROW_COUNT_MISMATCH")
    return {
        "dataset": dataset,
        "release": run,
        "source": "WRDS",
        "source_library": value["library"],
        "columns": [{**col, "dtype": "string"} for col in columns],
        "objects": parts,
        "manifest_key": key,
        "manifest_sha256": digest,
        "manifest_rows": value["row_count"],
        "PIT_status": "NOT_ESTABLISHED",
        "normalization": {
            "status": "SOURCE_TEXT_PRESERVED",
            "encoding": value["encoding"],
            "null_semantics": value["null_semantics"],
            "canonical_ready": False,
            "publication_state": "STAGED",
            "availability_time": None,
            "remaining_gates": [
                "DATASET_SPECIFIC_TYPES_AND_UNITS",
                "DATED_IDENTITY",
                "REVISION_AND_AVAILABILITY_POLICY",
                "LICENSED_USAGE_POLICY",
            ],
        },
    }


def read_json(s3, bucket, key, digest=None, limit=1048576):
    response = s3.get_object(Bucket=bucket, Key=key)
    with response["Body"] as body:
        if response["ContentLength"] > limit:
            raise ValueError("METADATA_BUDGET")
        raw = body.read(limit + 1)
    if len(raw) != response["ContentLength"] or len(raw) > limit:
        raise ValueError("METADATA_LENGTH_MISMATCH")
    actual = hashlib.sha256(raw).hexdigest()
    if digest is not None and actual != digest:
        raise ValueError("METADATA_HASH_MISMATCH")
    return json.loads(raw), actual


def extend(s3, base_key, base_sha):
    root, _ = read_json(s3, FINANCIAL, base_key, base_sha)
    if root.get("schema_version") != "zion-native-catalog-v1":
        raise ValueError("BASE_CATALOG_INVALID")

    def legacy(entry):
        ref = entry["reference"]
        if not ref["key"].startswith(base_key.removesuffix("manifest.json")):
            raise ValueError("BASE_REFERENCE_INVALID")
        d, _ = read_json(s3, FINANCIAL, ref["key"], ref["sha256"])
        return {
            "dataset": d["dataset"],
            "release": d["release"],
            "source": d["source"],
            "source_library": d["family"],
            "columns": d["columns"],
            "objects": d["objects"],
            "manifest_key": d["manifest"]["key"],
            "manifest_sha256": d["manifest"]["sha256"],
            "manifest_rows": d.get("rows_declared"),
            "PIT_status": d.get("PIT_status", "NOT_ESTABLISHED"),
            **({"normalization": d["normalization"]} if "normalization" in d else {}),
        }

    if len(root["entries"]) > 2000:
        raise ValueError("CATALOG_ENTRY_BUDGET")
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(legacy, root["entries"]))
    by_key = {r["manifest_key"]: r for r in records}
    print(json.dumps({"phase": "BASE_VERIFIED", "releases": len(records)}), flush=True)
    staged = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=PRIVATE, Prefix=STAGED):
        staged.extend(o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json"))
        if len(staged) > 2000:
            raise ValueError("STAGED_ENTRY_BUDGET")

    def completed(key):
        ref, _ = read_json(s3, PRIVATE, key, limit=16384)
        # Older preservation used a different receipt/schema. Keep an already
        # registered release, but never reinterpret its payload as text Parquet.
        if "manifest_key" in ref:
            prior = by_key.get(ref["manifest_key"])
            if prior and prior["manifest_sha256"] != ref.get("manifest_sha256"):
                raise ValueError("IMMUTABLE_MANIFEST_CHANGED")
            return None, {
                "marker": key,
                "error_class": "LEGACY_MARKER_ALREADY_REGISTERED"
                if prior
                else "LEGACY_MARKER_REQUIRES_RECONCILIATION",
            }
        if not re.fullmatch(
            r"raw/wrds/[a-z0-9_]+/releases/[A-Za-z0-9_-]+/manifest.json", ref.get("key", "")
        ) or not HEX.fullmatch(ref.get("sha256", "")):
            raise ValueError("STAGED_REFERENCE_INVALID")
        value, digest = read_json(s3, PRIVATE, ref["key"], ref["sha256"])
        return remote_record(value, ref["key"], digest), None

    added = 0
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for record, error in pool.map(completed, sorted(staged)):
            if error:
                errors.append(error)
                continue
            prior = by_key.get(record["manifest_key"])
            if prior and prior["manifest_sha256"] != record["manifest_sha256"]:
                raise ValueError("IMMUTABLE_MANIFEST_CHANGED")
            if not prior:
                by_key[record["manifest_key"]] = record
                added += 1
    # Content identity excludes scan time, making an unchanged rerun idempotent.
    inventory = {
        "releases": sorted(by_key.values(), key=lambda r: r["manifest_key"]),
        "generated_at": root["inventory"].get("generated_at"),
        "errors": errors,
        "parent_catalog": {"key": base_key, "sha256": base_sha},
        "scope": "EXISTING_CATALOG_PLUS_COMPLETED_REMOTE_PRESERVATION; PARTIAL_EXPORTS_EXCLUDED",
    }
    objects, metadata = publisher.build(
        inventory, publisher.sha(publisher.encode(inventory)), root.get("cross_domain_coverage", {})
    )
    metadata.update(
        added_releases=added,
        completed_remote_markers=len(staged),
        reconciliation_gaps=errors,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        raw_payload_bytes_read=0,
        normalized_financial_facts=False,
        parent_catalog=inventory["parent_catalog"],
    )
    return objects, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-key", required=True)
    parser.add_argument("--base-sha256", required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    import boto3
    from botocore.config import Config

    config = configparser.ConfigParser(interpolation=None)
    config.read(Path.home() / ".config/rclone/rclone.conf")
    r = config["r2"]
    s3 = boto3.client(
        "s3",
        endpoint_url=r["endpoint"],
        aws_access_key_id=r["access_key_id"],
        aws_secret_access_key=r["secret_access_key"],
        region_name="auto",
        config=Config(
            connect_timeout=10, read_timeout=20, retries={"max_attempts": 2}, max_pool_connections=4
        ),
    )
    objects, metadata = extend(s3, args.base_key, args.base_sha256)
    if args.publish:

        def put(key):
            raw = objects[key]
            try:
                s3.put_object(
                    Bucket=FINANCIAL,
                    Key=key,
                    Body=raw,
                    ContentType="application/json",
                    IfNoneMatch="*",
                )
            except s3.exceptions.ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                    raise
            read_json(s3, FINANCIAL, key, publisher.sha(raw))

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(put, [key for key in objects if key != metadata["key"]]))
        put(metadata["key"])
    metadata["publication"] = "IMMUTABLE_CANDIDATE_VERIFIED" if args.publish else "DRY_RUN"
    args.output_metadata.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
