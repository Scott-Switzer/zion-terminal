#!/usr/bin/env python3
"""Build/publish a metadata-only immutable coverage sidecar from pinned local packets.

Does not fetch providers, rewrite releases, or change any runtime configuration.
All counts use stored source cells, never an inferred calendar of expected values.
"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

SCHEMA = "zion-historical-coverage-v1"
DENOMINATOR = "STORED_SOURCE_CELLS_NOT_EXPECTED_CALENDAR"
PREFIX = "gold/historical-series/coverage/"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_verified(directory, relative, sha):
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Coverage source path outside store")
    raw = path.read_bytes()
    if digest(raw) != sha:
        raise ValueError("Coverage source hash mismatch")
    return json.loads(raw)


def build_coverage(directory, relative_manifest, manifest_sha256):
    directory = Path(directory)
    if not re.fullmatch(r"[a-f0-9]{64}", manifest_sha256):
        raise ValueError("Invalid manifest SHA")
    root = _read_verified(directory, relative_manifest, manifest_sha256)
    release = root["release_id"]
    if (
        root.get("schema_version") != "zion-historical-series-v1"
        or not re.fullmatch(r"[a-f0-9]{64}", release)
        or relative_manifest != f"releases/{release}/manifest.json"
        or len(root["series"]) != root["series_count"]
    ):
        raise ValueError("Coverage manifest reference mismatch")
    series = {}
    for sid, entry in sorted(root["series"].items()):
        expected_local = f"releases/{release}/" + entry["file"]
        if entry["local_path"] != expected_local or not re.fullmatch(
            r"series/[a-f0-9]{64}\.json", entry["file"]
        ):
            raise ValueError("Coverage packet reference mismatch")
        packet = _read_verified(directory, expected_local, entry["sha256"])
        if packet.get("series_id") != sid or packet.get("schema_version") != root["schema_version"]:
            raise ValueError("Coverage packet identity mismatch")
        if packet["columns"] != [
            "observation_date",
            "value_decimal",
            "missing_reason",
            "source_id",
        ]:
            raise ValueError("Coverage packet columns mismatch")
        rows = packet["rows"]
        if len({r[0] for r in rows}) != len(rows) or not rows:
            raise ValueError("Coverage packet dates invalid")
        dates = [r[0] for r in rows]
        if min(dates) != entry["date_min"] or max(dates) != entry["date_max"]:
            raise ValueError("Coverage packet date bounds mismatch")
        valid = [r[0] for r in rows if r[1] is not None]
        lo, hi = (min(valid), max(valid)) if valid else (None, None)
        c = dict(
            packet_sha256=entry["sha256"],
            valid_date_min=lo,
            valid_date_max=hi,
            total_cells=len(rows),
            populated_cells=len(valid),
            missing_cells=0,
            before_first_value=0,
            after_last_value=0,
            interior_missing=0,
            all_missing_cells=0,
            source_error_cells=0,
        )
        for date, value, reason, source in rows:
            if source not in root["sources"]:
                raise ValueError("Coverage source lineage missing")
            if value is not None:
                continue
            c["missing_cells"] += 1
            zone = (
                "all_missing_cells"
                if lo is None
                else "before_first_value"
                if date < lo
                else "after_last_value"
                if date > hi
                else "interior_missing"
            )
            c[zone] += 1
            c["source_error_cells"] += int(reason == "SOURCE_CELL_ERROR")
        if (
            c["missing_cells"] != entry["missing"]
            or c["populated_cells"] != entry["observations"]
            or c["total_cells"] != entry["rows"]
        ):
            raise ValueError("Coverage manifest counts mismatch")
        series[sid] = c
    populated = sum(c["populated_cells"] for c in series.values())
    missing = sum(c["missing_cells"] for c in series.values())
    if populated != root["observation_count"] or missing != root["missing_count"]:
        raise ValueError("Coverage root counts mismatch")
    totals = {
        k: sum(c[k] for c in series.values())
        for k in (
            "total_cells",
            "populated_cells",
            "missing_cells",
            "before_first_value",
            "after_last_value",
            "interior_missing",
            "all_missing_cells",
            "source_error_cells",
        )
    }
    return dict(
        schema_version=SCHEMA,
        release_id=release,
        manifest_sha256=manifest_sha256,
        denominator=DENOMINATOR,
        series_count=len(series),
        summary=totals,
        series=series,
        interpretation="Boundary blanks are outside the observed valid-value window; "
        "causal inception/discontinuation is not inferred. Interior gaps remain missing. "
        "Absent source rows and calendar completeness are not measured.",
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--upload", action="store_true", help="Publish immutable metadata to existing R2"
    )
    args = p.parse_args()
    sidecar = build_coverage(args.store, args.manifest, args.manifest_sha256)
    raw = encoded(sidecar)
    sha = digest(raw)
    key = PREFIX + sha + ".json"
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / (sha + ".json")
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Refuse to replace content-addressed coverage")
    path.write_bytes(raw)
    receipt = dict(
        status="CANDIDATE_VERIFIED",
        release_id=sidecar["release_id"],
        manifest_sha256=args.manifest_sha256,
        bytes=len(raw),
        sha256=sha,
        key=key,
        local_path=str(path.resolve()),
        summary=sidecar["summary"],
        environment={"HISTORICAL_COVERAGE_KEY": key, "HISTORICAL_COVERAGE_SHA256": sha},
        publication_pointer_writes=0,
    )
    if args.upload:
        remote = "r2:financial-system-datasets/" + key
        subprocess.run(["rclone", "copyto", "--immutable", str(path), remote], check=True)
        readback = subprocess.run(["rclone", "cat", remote], check=True, capture_output=True).stdout
        if digest(readback) != sha or readback != raw:
            raise ValueError("Coverage remote readback SHA mismatch")
        receipt.update(
            status="IMMUTABLE_METADATA_UPLOADED_VERIFIED",
            remote_sha256=sha,
            remote_bytes=len(readback),
        )
    (args.output / "receipt.json").write_bytes(encoded(receipt) + b"\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
