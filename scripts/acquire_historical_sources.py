"""Acquire all supported official historical sources; resume by verified raw-content hash.

Local bounded candidate acquisition; no production scheduling or CURRENT updates.
Use --publish only to upload immutable verified release objects to the existing bucket.
"""

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zion_terminal.history_sources import ecb, treasury, worldbank
from zion_terminal.history_store import acquire, atomic, build_release, download, encoded


def publish(directory, reference):
    """Existing R2 remote only; conditional immutable writes with exact readback."""
    import subprocess

    from zion_terminal.history_store import PREFIX, digest

    manifest_path = directory / reference["relative_manifest"]
    manifest_raw = manifest_path.read_bytes()
    if digest(manifest_raw) != reference["sha256"]:
        raise ValueError("Manifest preflight hash mismatch")
    manifest = json.loads(manifest_raw)
    release = reference["release_id"]
    prefix = PREFIX + release + "/"
    if manifest["release_id"] != release or reference["key"] != prefix + "manifest.json":
        raise ValueError("Manifest release identity mismatch")
    identity = {
        k: v
        for k, v in manifest.items()
        if k not in ("release_id", "series_count", "observation_count", "missing_count")
    }
    identity["series"] = {
        k: {field: value for field, value in entry.items() if field not in ("key", "local_path")}
        for k, entry in manifest["series"].items()
    }
    if digest(encoded(identity)) != release:
        raise ValueError("Manifest content identity mismatch")
    objects = []
    for series, entry in manifest["series"].items():
        name = "series/" + digest(series.encode()) + ".json"
        if entry["key"] != prefix + name or entry["local_path"] != f"releases/{release}/{name}":
            raise ValueError("Series reference mismatch")
        path = directory / entry["local_path"]
        raw = path.read_bytes()
        if digest(raw) != entry["sha256"] or len(raw) != entry["bytes"]:
            raise ValueError("Series preflight hash mismatch")
        objects.append((entry["key"], path, entry["sha256"]))
    sources = {}
    for source in manifest["sources"].values():
        sha = source["source_sha256"]
        path = directory / "raw" / (sha + ".bin")
        raw = path.read_bytes()
        if digest(raw) != sha or len(raw) != source.get("bytes", len(raw)):
            raise ValueError("Raw preflight hash mismatch")
        sources["raw/historical-sources/sha256/" + sha + ".bin"] = (path, sha)
    objects = (
        [(key, path, sha) for key, (path, sha) in sources.items()]
        + objects
        + [(reference["key"], manifest_path, reference["sha256"])]
    )

    def put(item):
        key, path, expected = item
        target = "r2:financial-system-datasets/" + key
        subprocess.run(
            ["rclone", "copyto", str(path), target, "--immutable", "--quiet"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        remote = subprocess.run(
            ["rclone", "cat", target], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ).stdout
        if hashlib.sha256(remote).hexdigest() != expected:
            raise ValueError("Immutable publication readback mismatch")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(put, objects[:-1]))
    put(objects[-1])
    return dict(
        objects_verified=len(objects),
        source_raw_objects=len(sources),
        publication_pointer_writes=0,
        total_bytes=sum(path.stat().st_size for _, path, _ in objects),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--reuse-probes", type=Path)
    args = parser.parse_args()
    adapters = {
        "treasury_nominal": treasury,
        "treasury_real": treasury,
        "ecb_fx": ecb,
        "worldbank_commodities": worldbank,
    }
    tasks = treasury.tasks() + ecb.tasks() + worldbank.tasks()
    seeds = {}
    if args.reuse_probes and not args.refresh:
        seeds[ecb.tasks()[0]["url"]] = args.reuse_probes / "ecb-eurofxref-hist.zip"
        seeds[worldbank.tasks()[0]["url"]] = args.reuse_probes / "CMO-Historical-Data-Monthly.xlsx"
        seeds[next(t["url"] for t in tasks if t["id"] == "treasury_nominal:2025")] = (
            args.reuse_probes / "treasury_nominal_2025.csv"
        )

    def fetch(url):
        if url in seeds and seeds[url].is_file():
            return dict(raw=seeds[url].read_bytes(), etag=None, last_modified=None)
        return download(url)

    result = acquire(
        tasks, adapters, args.directory, workers=args.workers, refresh=args.refresh, fetcher=fetch
    )
    summary = {k: result[k] for k in ("fetched_tasks", "cached_tasks", "failures")}
    summary.update(
        planned_tasks=len(tasks),
        normalized_cells=len(result["observations"]),
        status="COMPLETE" if not result["failures"] else "PARTIAL",
    )
    if result["observations"]:
        discovery = json.loads(
            (Path(__file__).resolve().parents[1] / "config/historical_sources.json").read_bytes()
        )["sources"]
        reference = build_release(
            result["observations"], result["sources"], args.directory, discovery=discovery
        )
        summary["release"] = reference
        if args.publish:
            if result["failures"]:
                raise ValueError("Refusing publication of unfinished acquisition")
            summary["publication"] = publish(args.directory, reference)
    atomic(args.directory / "acceptance.json", encoded(summary))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
