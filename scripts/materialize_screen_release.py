#!/usr/bin/env python3
"""Materialize the bounded latest cross-section without changing an old release.

The source release is copied server-side in R2; only the resolver, snapshots, and
annual artifacts are read locally to build one deterministic screen artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

BUCKET = "financial-system-datasets"
ROOT = "gold/serving"
CURRENT_KEY = "gold/serving/coverage25/CURRENT.json"
TEMPORAL = "5d225691cb60251d1997bb8d749267da845f0b3cda32137cbf76c3fbd2783b1d"
FIELDS = ("revenue", "operating_margin", "net_income", "last_price")


def rclone(*args: str, capture: bool = False) -> str:
    result = subprocess.run(["rclone", *args], check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def remote(key: str) -> str:
    return f"r2:{BUCKET}/{key}"


def read_json(key: str) -> object:
    return json.loads(rclone("cat", remote(key), capture=True))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def field_value(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "value": str(row.get("value_decimal", row.get("value"))) if row.get("value_decimal", row.get("value")) is not None else None,
        "unit": row.get("unit"),
        "available_at": row.get("available_at"),
        "evidence_id": row.get("evidence_id"),
        "source_type": "DERIVED" if row.get("derivation_type") not in (None, "reported", "reported_value") else "OBSERVED",
        "source_record_id": row.get("source_record_id"),
        "source_revision_id": row.get("source_revision_id"),
    }


def build_rows(index: dict, snapshots: dict[str, dict], annuals: dict[str, list[dict]]) -> list[dict]:
    rows = []
    for symbol, entry in sorted(index.get("symbols", {}).items()):
        path = entry.get("artifact_path")
        snapshot = snapshots.get(path)
        if not snapshot:
            continue
        annual = {row.get("metric_id"): row for row in annuals.get(path, [])}
        latest = snapshot.get("latest_annual") or {}
        # Snapshot latest values are already release-selected and preserve the
        # exact source lineage. Fall back to annual rows for older candidates.
        values = {}
        for field in FIELDS:
            row = latest.get(field) or annual.get(field)
            if field == "last_price":
                row = snapshot.get("latest_price")
            values[field] = field_value(row)
        rows.append({
            "entity_id": entry.get("entity"),
            "instrument_id": entry.get("instrument"),
            "listing_id": entry.get("listing"),
            "symbol": symbol,
            "values": values,
            "evidence_ids": sorted({v["evidence_id"] for v in values.values() if v and v.get("evidence_id")}),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-release", required=True)
    parser.add_argument("--apply", action="store_true", help="copy and upload the additive release")
    parser.add_argument("--promote", action="store_true", help="also atomically update CURRENT after apply")
    parser.add_argument("--updated-by", default="zion-tool-contract-v2-screen-materializer")
    parser.add_argument("--promote-existing", action="store_true", help="promote an already-uploaded candidate without copying release objects")
    args = parser.parse_args()
    if args.promote and not args.apply:
        parser.error("--promote requires --apply")
    source_id = args.source_release
    source_root = f"{ROOT}/releases/{source_id}"
    source_manifest = read_json(f"{source_root}/manifest.json")
    if source_manifest.get("serving_release_id") != source_id:
        raise SystemExit("source manifest identity mismatch")
    index = read_json(f"{source_root}/identity/resolver_index.json")
    paths = sorted({entry.get("artifact_path") for entry in index.get("symbols", {}).values() if entry.get("artifact_path")})
    snapshots = {path: read_json(f"{source_root}/{path}/snapshot.json") for path in paths}
    annuals = {path: read_json(f"{source_root}/{path}/fundamentals/annual.json") for path in paths}
    rows = build_rows(index, snapshots, annuals)
    payload = {
        "schema_version": "financial-serving-screen-v1",
        "serving_release_id": None,
        "temporal_schema_version": "financial-temporal-v1",
        "temporal_contract_sha256": TEMPORAL,
        "as_of_semantics": "latest_available_in_release",
        "fields": list(FIELDS),
        "rows": rows,
    }
    screen_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    screen_hash = hashlib.sha256(screen_bytes).hexdigest()
    release_id = hashlib.sha256(f"{source_id}|screen-v1|{screen_hash}".encode()).hexdigest()[:32]
    payload["serving_release_id"] = release_id
    screen_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / release_id
        screen = root / "screens/latest.json"
        screen.parent.mkdir(parents=True)
        screen.write_bytes(screen_bytes)
        manifest = dict(source_manifest)
        manifest["serving_release_id"] = release_id
        manifest["previous_serving_release"] = source_id
        manifest["builder"] = "materialize_screen_release.py"
        manifest["screen_entity_count"] = len(rows)
        manifest["artifacts"] = list(source_manifest.get("artifacts", [])) + [{"path": "screens/latest.json", "bytes": screen.stat().st_size, "rows": len(rows), "sha256": digest(screen)}]
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        current = {"schema_version": manifest["schema_version"], "serving_release_id": release_id, "manifest_key": f"{ROOT}/releases/{release_id}/manifest.json", "manifest_sha256": digest(manifest_path), "promoted_at": None, "updated_by": args.updated_by}
        print(json.dumps({"mode": "APPLY" if args.apply else "DRY_RUN", "source_release": source_id, "candidate_release": release_id, "screen_rows": len(rows), "screen_sha256": digest(screen), "manifest_sha256": digest(manifest_path)}, sort_keys=True))
        if not args.apply:
            return 0
        if not args.promote_existing:
            # Existing objects are copied to a new immutable prefix. The source
            # release is never overwritten and CURRENT is last.
            rclone("copy", remote(source_root), remote(f"{ROOT}/releases/{release_id}"), "--immutable")
            rclone("copyto", str(screen), remote(f"{ROOT}/releases/{release_id}/screens/latest.json"))
            rclone("copyto", str(manifest_path), remote(f"{ROOT}/releases/{release_id}/manifest.json"))
        if args.promote:
            current["promoted_at"] = "atomic-pointer-update"
            pointer = root / "CURRENT.json"
            pointer.write_text(json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n")
            rclone("copyto", str(pointer), remote(CURRENT_KEY))
        print(json.dumps({"status": "published", "current_updated": args.promote, "candidate_release": release_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
