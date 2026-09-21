from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import shlex
import subprocess
import tempfile
from pathlib import Path

BUCKET = "financial-system-datasets"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wrangler() -> list[str]:
    return shlex.split(os.environ.get("WRANGLER_BIN", "wrangler"))


def put(key: str, path: Path) -> None:
    subprocess.run([*_wrangler(), "r2", "object", "put", f"{BUCKET}/{key}", "--remote", "--file", str(path)], check=True)


def get(key: str) -> bytes:
    result = subprocess.run([*_wrangler(), "r2", "object", "get", "--remote", "--pipe", f"{BUCKET}/{key}"], check=True, capture_output=True)
    return result.stdout


def publish(export: Path, qc_path: Path, world_id: str, *, staging: bool) -> str:
    if not staging:
        raise ValueError("publishing requires --staging")
    qc = json.loads(qc_path.read_text())
    if qc.get("status") != "PASS" or qc.get("world_id") != world_id:
        raise ValueError("QC report must be PASS for the requested world")
    source_manifest = json.loads((export / "manifest.json").read_text())
    if source_manifest.get("world", {}).get("world_id") != world_id:
        raise ValueError("export manifest world does not match requested world")
    public = export / "public"
    required = [public / name for name in ("entities.json", "financials.json", "prices.json", "events.json")]
    if not all(path.is_file() for path in required):
        raise ValueError("native export is missing a required public artifact")
    with tempfile.TemporaryDirectory(prefix="zion-synthetic-publish-") as temp:
        root = Path(temp)
        release = root / "release"
        (release / "public").mkdir(parents=True)
        for path in required:
            shutil.copy2(path, release / "public" / path.name)
        hashes = {f"public/{path.name}": {"sha256": digest(release / "public" / path.name), "size_bytes": (release / "public" / path.name).stat().st_size} for path in required}
        release_manifest = {
            "schema_version": "1",
            "world": source_manifest["world"],
            "producer": source_manifest["producer"],
            "seed": source_manifest.get("seed"),
            "generated_at": source_manifest.get("generated_at"),
            "qc_status": "PASS",
            "qc_world_version": qc.get("world_version"),
            "qc_validator_version": qc.get("validator_version"),
            "qc_report_sha256": digest(qc_path),
            "artifact_hashes": hashes,
        }
        manifest_path = release / "manifest.json"
        manifest_path.write_text(json.dumps(release_manifest, sort_keys=True, separators=(",", ":")))
        release_id = digest(manifest_path)
        prefix = f"gold/synthetic-worlds/releases/{release_id}"
        serving_files = [manifest_path, *(release / "public" / path.name for path in required)]
        for path in serving_files:
            put(f"{prefix}/{path.relative_to(release)}", path)
        qc_copy = release / "qc_certification.json"
        shutil.copy2(qc_path, qc_copy)
        put(f"{prefix}/qc_certification.json", qc_copy)

        # R2 is strongly consistent. Read every immutable object back before
        # publishing the tiny world pointer; CURRENT is the commit marker.
        for path in (*serving_files, qc_copy):
            key = f"{prefix}/{path.relative_to(release)}"
            remote = get(key)
            expected = path.read_bytes()
            if remote != expected or digest(path) != hashlib.sha256(remote).hexdigest():
                raise RuntimeError(f"remote publication verification failed: {key}")

        pointer = root / "CURRENT.json"
        pointer.write_text(json.dumps({"world_id": world_id, "version": source_manifest["world"].get("version"), "prefix": prefix, "release_id": release_id, "qc_status": "PASS"}, sort_keys=True, separators=(",", ":")))
        put(f"control/synthetic-worlds/{world_id}/CURRENT.json", pointer)
    return prefix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world-export", type=Path, required=True)
    parser.add_argument("--qc-report", type=Path, required=True)
    parser.add_argument("--target-world", required=True)
    parser.add_argument("--staging", action="store_true")
    args = parser.parse_args()
    print(publish(args.world_export, args.qc_report, args.target_world, staging=args.staging))


if __name__ == "__main__":
    main()
