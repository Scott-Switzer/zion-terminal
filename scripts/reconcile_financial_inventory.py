"""Metadata-only reconciliation. Never downloads Parquet payloads or changes pointers."""

import argparse
import configparser
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


def client():
    import boto3
    from botocore.config import Config

    c = configparser.ConfigParser(interpolation=None)
    c.read(Path.home() / ".config/rclone/rclone.conf")
    v = c["r2"]
    return boto3.client(
        "s3",
        endpoint_url=v["endpoint"],
        aws_access_key_id=v["access_key_id"],
        aws_secret_access_key=v["secret_access_key"],
        region_name="auto",
        config=Config(max_pool_connections=8, retries={"max_attempts": 3}),
    )


def classify_error(error, objects):
    key = error["key"]
    present = objects.get(key)
    if present:
        if error["error_class"] == "ValueError" and present["bytes"] > 1048576:
            return "MANIFEST_EXCEEDED_OLD_1MIB_METADATA_LIMIT"
        return "OBJECT_NOW_PRESENT_REQUIRES_MANIFEST_REVALIDATION"
    prefix = key.rsplit("/", 1)[0] + "/"
    candidates = [
        k
        for k in objects
        if k.startswith(prefix) and ("manifest" in k.lower() or k.endswith("metadata.json"))
    ]
    if candidates:
        return "EXPECTED_FILENAME_MISSING_ALTERNATE_METADATA_PRESENT"
    if any(k.startswith(prefix) for k in objects):
        return "RELEASE_HAS_OBJECTS_WITHOUT_EXPECTED_MANIFEST"
    return "RELEASE_PREFIX_NOT_PRESENT_NOW"


def run(inventory_path, out):
    out.mkdir(parents=True, exist_ok=True)
    raw = inventory_path.read_bytes()
    inv = json.loads(raw)
    s = client()
    bucket = "financial-data-wrds-private"
    checkpoint = out / "wrds-object-metadata.json"
    objects = {}
    pages = 0
    if checkpoint.exists():
        objects = json.loads(checkpoint.read_text())
    else:
        cursor = None
        while True:
            args = {"Bucket": bucket, "Prefix": "raw/wrds/", "MaxKeys": 1000}
            if cursor:
                args["ContinuationToken"] = cursor
            page = s.list_objects_v2(**args)
            pages += 1
            for o in page.get("Contents", []):
                objects[o["Key"]] = {"bytes": o["Size"], "etag": o["ETag"].strip('"')}
            if pages % 25 == 0:
                print(
                    json.dumps(
                        {"phase": "metadata_inventory", "pages": pages, "objects": len(objects)}
                    ),
                    flush=True,
                )
            cursor = page.get("NextContinuationToken")
            if not page.get("IsTruncated"):
                break
        checkpoint.write_text(json.dumps(objects, separators=(",", ":")))
        checkpoint.chmod(0o600)
    references = []
    manifests = []

    def verify(record):
        key = record["manifest_key"]
        entry = objects.get(key)
        if not entry:
            return {"key": key, "status": "MISSING"}
        if entry["bytes"] > 2 * 1048576:
            return {"key": key, "status": "METADATA_BUDGET_EXCEEDED"}
        raw = s.get_object(Bucket=bucket, Key=key, IfMatch='"' + entry["etag"] + '"')["Body"].read(
            2 * 1048576 + 1
        )
        return {
            "key": key,
            "status": "SHA_MATCH"
            if hashlib.sha256(raw).hexdigest() == record["manifest_sha256"]
            else "SHA_CHANGED",
            "bytes": len(raw),
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        manifests = list(pool.map(verify, inv["releases"]))
    for record in inv["releases"]:
        for obj in record["objects"]:
            object_key = obj.get("key")
            actual = objects.get(object_key) if object_key else None
            declared = obj.get("bytes", obj.get("size_bytes"))
            state = (
                "UNRESOLVED_LEGACY_PATH_REFERENCE"
                if not object_key
                else "MISSING"
                if not actual
                else "PRESENT_DECLARED_SIZE_UNAVAILABLE"
                if declared is None
                else "SIZE_MISMATCH"
                if actual["bytes"] != declared
                else "PRESENT_SIZE_MATCH"
            )
            references.append(
                {
                    "key": object_key,
                    "declared_path": obj.get("path"),
                    "declared_bytes": declared,
                    "current": actual,
                    "reconciliation": state,
                    "analytical_queryability": "NOT_VERIFIED_BY_ROW_SCAN",
                    "format_eligible": bool(object_key and object_key.endswith(".parquet"))
                    and bool(actual and actual["bytes"] >= 8),
                }
            )
    errors = [
        {
            **e,
            "current_classification": classify_error(e, objects),
            "current_metadata": objects.get(e["key"]),
        }
        for e in inv["errors"]
    ]
    pointers = {}
    for b, key in [
        ("financial-system-datasets", "gold/serving/coverage25/CURRENT.json"),
        ("financial-system-datasets", "control/market-terminal/CURRENT.json"),
        (bucket, "control/full-financial-v1/wrds/CURRENT.json"),
    ]:
        try:
            data = s.get_object(Bucket=b, Key=key)["Body"].read(1048576)
            pointers[b + "/" + key] = hashlib.sha256(data).hexdigest()
        except s.exceptions.ClientError:
            pointers[b + "/" + key] = "ABSENT"
    summary = {
        "schema_version": "financial-estate-reconciliation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inventory_sha256": hashlib.sha256(raw).hexdigest(),
        "inventory_snapshot": inv["generated_at"],
        "scope": (
            "FULL_raw/wrds_NAMESPACE_METADATA;OTHER_STORES_AND_ALL_FILE_DECODABILITY_NOT_CENSUSED"
        ),
        "listed_objects": len(objects),
        "listed_bytes": sum(o["bytes"] for o in objects.values()),
        "registered_releases": len(inv["releases"]),
        "registered_object_references": len(references),
        "unique_referenced_objects": len({r["key"] for r in references if r["key"]}),
        "manifest_states": dict(Counter(r["status"] for r in manifests)),
        "object_states": dict(Counter(r["reconciliation"] for r in references)),
        "historical_error_classes": dict(Counter(e["error_class"] for e in errors)),
        "current_error_classifications": dict(Counter(e["current_classification"] for e in errors)),
        "queryability_gate": "FAIL_NOT_ALL_FOOTERS_OR_CODECS_VERIFIED",
        "entitlement": "STORAGE_ACCESS_DOES_NOT_ESTABLISH_MIGRATION_OR_REDISTRIBUTION_RIGHTS",
        "pointers_before": pointers,
        "provider_requests": 0,
        "licensed_data_rows_read": 0,
        "publication_pointer_mutations": 0,
    }
    for name, value in [
        (
            "inventory-reconciliation.json",
            {"summary": summary, "manifests": manifests, "objects": references, "errors": errors},
        ),
        ("inventory-summary.json", summary),
    ]:
        p = out / name
        p.write_text(json.dumps(value, indent=2) + "\n")
        p.chmod(0o600)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.inventory, a.output)
