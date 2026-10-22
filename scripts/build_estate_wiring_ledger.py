"""Build a deterministic, metadata-only ledger; never register or query data.

Inputs are an existing WRDS inventory and a saved key -> {bytes, etag} census.
The ledger accounts for every physical namespace and maps catalog registration
only by exact inventory manifest keys. File presence is not row-query proof.
No network, provider execution, R2 writes, credentials or row payloads are used.
"""

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def namespace(key):
    if not isinstance(key, str) or "\\" in key or "\x00" in key:
        raise ValueError("Invalid census key")
    parts = key.split("/")
    if parts[:2] != ["raw", "wrds"] or len(parts) < 4:
        raise ValueError("Census key outside raw/wrds scope")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("Unsafe census path")
    return parts[2]


def role(key):
    if key.endswith("/manifest.json"):
        return "source_manifest"
    if key.endswith(".parquet"):
        return "parquet"
    if key.endswith(".started"):
        return "acquisition_marker"
    if key.endswith(".json"):
        return "other_json"
    return "other_file"


def build(inventory, census):
    """Return metadata evidence without upgrading catalog or queryability claims."""
    groups = defaultdict(dict)
    registrations = defaultdict(list)
    errors = defaultdict(list)
    for key, record in census.items():
        name = namespace(key)
        if (not isinstance(record, dict) or type(record.get("bytes")) is not int
                or record["bytes"] < 0):
            raise ValueError("Invalid object byte count")
        groups[name][key] = {"bytes": record["bytes"], "etag": record.get("etag")}
    manifest_keys = set()
    for record in inventory["releases"]:
        key = record["manifest_key"]
        name = namespace(key)
        if not key.endswith("/manifest.json") or key in manifest_keys:
            raise ValueError("Invalid or duplicated registered manifest")
        manifest_keys.add(key)
        pin = record["manifest_sha256"]
        if not isinstance(pin, str) or not re.fullmatch(r"[a-f0-9]{64}", pin):
            raise ValueError("Invalid manifest hash")
        registrations[name].append(record)
    for error in inventory.get("errors", []):
        key = error.get("key")
        if isinstance(key, str) and key.startswith("raw/wrds/"):
            errors[namespace(key)].append({"key": key, "error_class": error.get("error_class")})
    entries = []
    for name in sorted(set(groups) | set(registrations) | set(errors)):
        files = groups[name]
        registered = sorted(registrations.get(name, []), key=lambda r: r["manifest_key"])
        present_manifests = sorted(k for k in files if role(k) == "source_manifest")
        expected_manifests = {r["manifest_key"] for r in registered}
        for key in files:
            parts = key.split("/")
            if len(parts) > 5 and parts[3] == "releases":
                expected_manifests.add("/".join(parts[:5]) + "/manifest.json")
        missing_manifests = sorted(expected_manifests - set(files))
        missing_objects = set()
        unresolved = 0
        mapping = []
        for record in registered:
            for obj in record.get("objects", []):
                key = obj.get("key")
                if not isinstance(key, str) or not key.startswith("raw/wrds/"):
                    unresolved += 1
                elif key not in census:
                    missing_objects.add(key)
            mapping.append({
                "declared_dataset": record.get("dataset"),
                "release": record.get("release"),
                "family": record.get("source_library"),
                "manifest_key": record["manifest_key"],
                "manifest_sha256": record["manifest_sha256"],
                "manifest_present_in_census": record["manifest_key"] in files,
                "catalog_id": digest(record["manifest_key"].encode())[:32],
                "catalog_metadata_registered": True,
                "declared_object_references": len(record.get("objects", [])),
                "declared_rows": record.get("manifest_rows"),
                "PIT_status": record.get("PIT_status", "NOT_ESTABLISHED"),
            })
        native = bool(registered) and not missing_manifests and not missing_objects and not unresolved
        entries.append({
            "dataset_prefix": name,
            "prefix": "raw/wrds/" + name + "/",
            "store": "research",
            "status": "native-access" if native else "unknown",
            "status_basis": (
                "REGISTERED_SOURCE_METADATA_NATIVE_ROUTE_CAPABILITY_ONLY"
                if native else "UNREGISTERED_OR_INCOMPLETE_METADATA_NOT_QUERY_CERTIFIED"
            ),
            "semantic_api_status": "unserved",
            "row_query_verified": False,
            "physical_layer": "raw_source_namespace",
            "objects": len(files),
            "bytes": sum(v["bytes"] for v in files.values()),
            "role_counts": dict(sorted(Counter(role(k) for k in files).items())),
            "registration": mapping,
            "manifest_metadata": [dict(key=k, **files[k]) for k in present_manifests],
            "manifest_discovery_candidates": sorted(set(present_manifests) - manifest_keys),
            "missing_manifest_keys": missing_manifests,
            "missing_registered_objects": sorted(missing_objects),
            "unresolved_registered_object_references": unresolved,
            "historical_inventory_errors": sorted(errors[name], key=lambda e: e["key"]),
            "native_object_listing_route": "/private/v1/datasets/research/objects",
            "native_schema_route": "/private/v1/datasets/research/schema",
            "native_rows_route": "/private/v1/datasets/research/rows",
            "native_query_limits": "PARQUET_CODEC_SCHEMA_AND_BYTE_BUDGETS_APPLY",
        })
    return {
        "schema_version": "zion-estate-wiring-ledger-v1",
        "scope": "SAVED_METADATA_ONLY;NO_ROW_OR_FOOTER_READS;NO_REGISTRATION_MUTATIONS",
        "inventory_generated_at": inventory.get("generated_at"),
        "input_content_hashes": {
            "inventory_canonical_sha256": digest(encoded(inventory)),
            "census_canonical_sha256": digest(encoded(census)),
        },
        "summary": {
            "dataset_prefixes": len(entries),
            "registered_named_datasets": len({
                r["dataset"] for r in inventory["releases"] if r.get("dataset")
            }),
            "registered_prefixes": len(registrations),
            "registered_releases": len(manifest_keys),
            "unregistered_prefixes": sum(not e["registration"] for e in entries),
            "objects": sum(e["objects"] for e in entries),
            "bytes": sum(e["bytes"] for e in entries),
            "status_counts": dict(sorted(Counter(e["status"] for e in entries).items())),
            "missing_manifest_count": sum(len(e["missing_manifest_keys"]) for e in entries),
            "manifest_discovery_candidates": sum(
                len(e["manifest_discovery_candidates"]) for e in entries
            ),
            "provider_requests": 0,
            "data_rows_read": 0,
            "r2_writes": 0,
        },
        "datasets": entries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--census-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_inventory = args.inventory.read_bytes()
    raw_census = args.census.read_bytes()
    if (digest(raw_inventory) != args.inventory_sha256
            or digest(raw_census) != args.census_sha256):
        raise ValueError("Input metadata hash mismatch")
    ledger = build(json.loads(raw_inventory), json.loads(raw_census))
    output = encoded(ledger) + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    print(json.dumps(dict(ledger["summary"], ledger_sha256=digest(output)), sort_keys=True))


if __name__ == "__main__":
    main()
