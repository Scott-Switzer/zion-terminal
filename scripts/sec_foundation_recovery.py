"""Bounded metadata recovery and explicit quarantine; never edits archives/catalog pointers."""

import hashlib
import json
import re
from pathlib import Path

from reconcile_financial_inventory import client

BASE = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-financial-foundation")
OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-financial-history")


def bounded_manifest(s, bucket, key, maximum=8 * 1048576):
    head = s.head_object(Bucket=bucket, Key=key)
    if not 0 < head["ContentLength"] <= maximum:
        raise ValueError("MANIFEST_BUDGET_EXCEEDED")
    response = s.get_object(Bucket=bucket, Key=key, IfMatch=head["ETag"])
    try:
        raw = response["Body"].read(maximum + 1)
    finally:
        response["Body"].close()
    if len(raw) != head["ContentLength"] or len(raw) > maximum:
        raise ValueError("MANIFEST_LENGTH_MISMATCH")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("MANIFEST_SCHEMA_UNRECOGNIZED")
    return result, {
        "key": key,
        "bytes": len(raw),
        "etag": head["ETag"].strip('"'),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main():
    report = json.loads((BASE / "inventory-reconciliation.json").read_text())
    objects = json.loads((BASE / "wrds-object-metadata.json").read_text())
    s = client()
    recovered = []
    quarantine = []
    for error in report["errors"]:
        if error["error_class"] == "ValueError":
            value, proof = bounded_manifest(s, "financial-data-wrds-private", error["key"])
            # Preserve source metadata, report shape only. Never invent a producer contract.
            proof.update(
                status="RECOVERED_VALID_JSON_SCHEMA_REQUIRES_SOURCE_CONTRACT",
                root_keys=sorted(value),
                promoted=False,
            )
            for field in ["parts", "objects", "files"]:
                if isinstance(value.get(field), list):
                    proof[field + "_declared"] = len(value[field])
            parts = value.get("parts", [])
            if str(value.get("schema_version")) != "2" or not isinstance(parts, list):
                proof["status"] = "QUARANTINED_UNKNOWN_MANIFEST_SCHEMA"
            else:
                states = {}
                total_rows = 0
                for part in parts:
                    key = part.get("key")
                    current = objects.get(key)
                    state = (
                        "PRESENT_SIZE_MATCH"
                        if current and current["bytes"] == part.get("bytes")
                        else "MISSING_OR_SIZE_MISMATCH"
                    )
                    if not re.fullmatch("[a-f0-9]{64}", str(part.get("sha256", ""))):
                        state = "INVALID_DECLARED_HASH"
                    states[state] = states.get(state, 0) + 1
                    total_rows += part.get("rows", 0)
                keys_unique = len({part.get("key") for part in parts}) == len(parts)
                proof.update(
                    part_reference_states=states,
                    part_keys_unique=keys_unique,
                    reference_validation_scope="PRIOR_METADATA_CENSUS_CHECKPOINT_NOT_FULL_PART_REHASH",
                    rows_sum_matches=total_rows == value.get("row_count"),
                    hashes_status="SOURCE_DECLARED_NOT_REHASHED",
                )
                proof["status"] = (
                    "RECOVERED_VALIDATED_METADATA_ONLY"
                    if set(states) == {"PRESENT_SIZE_MATCH"}
                    and proof["rows_sum_matches"]
                    and keys_unique
                    else "QUARANTINED_INVALID_PART_REFERENCES"
                )
            recovered.append(proof)
        else:
            prefix = error["key"].rsplit("/", 1)[0] + "/"
            members = [(k, v) for k, v in objects.items() if k.startswith(prefix)]
            quarantine.append(
                {
                    "expected_manifest": error["key"],
                    "status": "QUARANTINED_UNSEALED_RELEASE",
                    "objects_present": len(members),
                    "bytes_present": sum(v["bytes"] for k, v in members),
                    "alternate_metadata_objects": [
                        k
                        for k, v in members
                        if "manifest" in k.lower() or k.endswith("metadata.json")
                    ],
                    "producer_cause": "NOT_ESTABLISHED",
                    "promotion_allowed": False,
                }
            )
    legacy = [
        x for x in report["objects"] if x["reconciliation"] == "UNRESOLVED_LEGACY_PATH_REFERENCE"
    ]
    result = {
        "oversize_manifests": recovered,
        "unsealed_releases": quarantine,
        "legacy_references": [
            {
                "declared_path": x["declared_path"],
                "bytes_declared": x["declared_bytes"],
                "status": "QUARANTINED_AMBIGUOUS_RELATIVE_REFERENCE",
                "promotion_allowed": False,
            }
            for x in legacy
        ],
        "archive_writes": 0,
        "publication_pointer_changes": 0,
        "full_archive_queryability": "FAIL_NOT_FULLY_CENSUSED",
    }
    OUT.mkdir(mode=0o700, exist_ok=True)
    (OUT / "foundation-recovery.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "recovered": len(recovered),
                "quarantined_unsealed": len(quarantine),
                "quarantined_legacy": len(legacy),
                "oversize_root_shapes": [x["root_keys"] for x in recovered],
            }
        )
    )


if __name__ == "__main__":
    main()
