"""Ingest the newest SEC Company Facts bulk archive as a new immutable research release.

The September 24, 2026 snapshot that the engine and readiness releases are pinned to is a
historical point-in-time source. Valuations dated after it cannot be presented as decided on
current filings, so a newer release has to exist alongside it rather than replacing it. This
script retrieves the current public bulk archive, hashes it while it streams, publishes it to
the private research bucket under a dated immutable key, derives the member index the engine
and readiness runs need as a pin, and records the difference from the pinned snapshot.

Nothing already published is rewritten, and no CURRENT pointer is promoted: a release becomes
serving only through the existing quality and authorization gates.

    python3 scripts/sec_bulk_release_refresh.py --probe
    python3 scripts/sec_bulk_release_refresh.py --publish
"""

import argparse
import hashlib
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

ENGINE = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale")
OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-bulk-refresh")
BUCKET = "financial-data-wrds-private"
PREFIX = "sec/bulk/sec-nightly-"
URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
IDENTITY = "Zion Financial Research (research contact: scswitzer@chapman.edu)"
SCHEMA_VERSION = "sec-bulk-release-v1"
CHUNK = 8 * 1048576
# One Class A write plus the first month of retained storage at the published rates.
ESTIMATED_FIRST_MONTH_USD = 0.005 + 0.015


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=str).encode()


def head(source=URL):
    request = urllib.request.Request(source, method="HEAD", headers={"User-Agent": IDENTITY})
    with urllib.request.urlopen(request, timeout=60) as response:
        return {
            "url": source,
            "bytes": int(response.headers["Content-Length"]),
            "last_modified": response.headers["Last-Modified"],
            "etag": response.headers["ETag"].strip('"'),
            "probed_utc": datetime.now(timezone.utc).isoformat(),
        }


class HashingStream(io.RawIOBase):
    """The SEC response body, hashed as the upload reads it: no local archive copy."""

    def __init__(self, response):
        self.response = response
        self.sha = hashlib.sha256()
        self.md5 = hashlib.md5()
        self.read_bytes = 0

    def readable(self):
        return True

    def read(self, size=-1):
        block = self.response.read(CHUNK if size is None or size < 0 else size)
        if block:
            self.sha.update(block)
            self.md5.update(block)
            self.read_bytes += len(block)
        return block


def pinned_snapshot():
    base = json.loads((ENGINE / "engine-manifest.json").read_text())
    entries = json.loads((ENGINE / "full-member-index.json").read_text())
    return base, entries


def readback(source, client, key):
    """Read the stored object once: its digest is the release pin, not the upload claim."""
    from sec_universe_readiness import BLOCK, RangeReader
    reader = RangeReader({"bucket": BUCKET, "key": key, "bytes": source["bytes"],
                          "etag": source["etag"]}, client, hash_blocks=True)
    observed_md5 = hashlib.md5()
    while True:
        block = reader.read(BLOCK)
        if not block:
            break
        observed_md5.update(block)
    return {
        "sha256": reader.hasher.hexdigest(),
        "md5": observed_md5.hexdigest(),
        "blocks": reader.requests,
        "hash_contiguous": reader.next_hash_block == (source["bytes"] + BLOCK - 1) // BLOCK,
        "etag_matches_readback_md5": observed_md5.hexdigest() == source["etag"],
    }


def member_index(source, client, key):
    """Read the new archive's own central directory and pin every member it lists."""
    from sec_universe_readiness import RangeReader
    reader = RangeReader({"bucket": BUCKET, "key": key, "bytes": source["bytes"],
                          "etag": source["etag"]}, client)
    with ZipFile(reader) as zf:
        index = {
            info.filename: {
                "offset": info.header_offset, "compression": info.compress_type,
                "flags": 0, "crc": info.CRC, "compressed_bytes": info.compress_size,
                "uncompressed_bytes": info.file_size, "name": info.filename,
            }
            for info in zf.infolist()
        }
        requests = reader.requests
    return index, requests


def normalize(index):
    """Key a member index by bare CIK.

    The archived index is keyed by CIK while a ZIP member is named `CIK##########.json`, so a
    raw key comparison reports every member as both added and removed. Keying both sides by CIK
    is what makes the difference a statement about issuers.
    """
    import re
    return {(re.fullmatch(r"CIK(\d{10})\.json", name).group(1) if name.startswith("CIK")
             else name): entry for name, entry in index.items()}


def difference(previous, current):
    previous, current = normalize(previous), normalize(current)
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    changed = sorted(
        name for name in set(previous) & set(current)
        if (previous[name]["crc"], previous[name]["uncompressed_bytes"])
        != (current[name]["crc"], current[name]["uncompressed_bytes"]))
    return {
        "members_previous": len(previous), "members_current": len(current),
        "members_added": len(added), "members_removed": len(removed),
        "members_changed": len(changed),
        "uncompressed_bytes_previous": sum(e["uncompressed_bytes"] for e in previous.values()),
        "uncompressed_bytes_current": sum(e["uncompressed_bytes"] for e in current.values()),
        "sample_added": added[:10], "sample_removed": removed[:10], "sample_changed": changed[:10],
    }


def recheck_diff(client, snapshot, previous_entries):
    """Correct a manifest whose issuer comparison compared different key names.

    The archive bytes, their readback hash and the member index are unchanged; only the
    difference calculation was wrong, so the correction is published beside the original
    instead of overwriting it.
    """
    base = PREFIX + snapshot.replace("-", "")
    index_object = client.get_object(Bucket=BUCKET, Key=base + "/member-index.json")
    try:
        index_raw = index_object["Body"].read()
    finally:
        index_object["Body"].close()
    index = json.loads(index_raw)
    corrected = difference(previous_entries, index)
    manifest_object = client.get_object(Bucket=BUCKET, Key=base + "/manifest.json")
    try:
        manifest = json.loads(manifest_object["Body"].read())
    finally:
        manifest_object["Body"].close()
    manifest["difference_from_pinned_snapshot"] = corrected
    manifest["difference_correction"] = (
        "The first manifest compared the archived CIK-keyed index with filename-keyed members "
        "and reported every issuer as added and removed. This corrected difference keys both "
        "by CIK; the archive hash, member index and publication receipts are unchanged.")
    manifest["member_index_sha256_observed"] = digest(index_raw)
    raw = encoded(manifest)
    key = base + "/manifest-corrected.json"
    client.put_object(Bucket=BUCKET, Key=key, Body=raw, IfNoneMatch="*")
    head = client.head_object(Bucket=BUCKET, Key=key)
    manifest["publication_receipt"] = {
        "manifest_key": key, "bytes": len(raw), "sha256": digest(raw),
        "readback": "HEAD_ETAG_MD5_MATCH"
        if head["ETag"].strip('"') == hashlib.md5(raw).hexdigest() else "MISMATCH"}
    directory = OUT / snapshot
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_bytes(raw + b"\n")
    (directory / "member-index.json").write_bytes(index_raw + b"\n")
    print(json.dumps({"key": key, "difference": corrected,
                      "issuers_added": corrected["members_added"],
                      "issuers_removed": corrected["members_removed"],
                      "issuers_changed": corrected["members_changed"]}, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--probe", action="store_true", help="report the source only")
    parser.add_argument("--recheck-diff", action="store_true",
                        help="recompute the issuer difference from the published member index")
    parser.add_argument("--url", default=URL)
    args = parser.parse_args()

    base, previous_entries = pinned_snapshot()
    source = head(args.url)
    snapshot = datetime.strptime(source["last_modified"], "%a, %d %b %Y %H:%M:%S %Z").strftime(
        "%Y-%m-%d")
    key = PREFIX + snapshot.replace("-", "") + "/companyfacts.zip"
    print(json.dumps({"source": source, "snapshot": snapshot, "key": key,
                      "pinned_snapshot": base["source"]["snapshot_date"],
                      "pinned_bytes": base["source"]["bytes"],
                      "newer_than_pinned": source["bytes"] != base["source"]["bytes"]}, indent=1))
    if args.probe:
        return

    from reconcile_financial_inventory import client
    s = client()
    if args.recheck_diff:
        recheck_diff(s, snapshot, previous_entries)
        return
    directory = OUT / snapshot
    directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        stored = s.head_object(Bucket=BUCKET, Key=key)
    except Exception:  # noqa: BLE001 - absent object is the normal first-run path
        request = urllib.request.Request(args.url, headers={"User-Agent": IDENTITY})
        with urllib.request.urlopen(request, timeout=900) as response:
            stream = HashingStream(response)
            s.put_object(Bucket=BUCKET, Key=key, Body=stream, ContentLength=source["bytes"],
                         IfNoneMatch="*")
        stored = s.head_object(Bucket=BUCKET, Key=key)
        streamed = {"sha256": stream.sha.hexdigest(), "md5": stream.md5.hexdigest(),
                    "bytes_streamed": stream.read_bytes}
    else:
        # A previous invocation stored the object but stopped before recording its manifest.
        # The stored bytes are the release, so they are read back and hashed rather than
        # trusted from the upload, and nothing is written twice.
        streamed = {"resumed_from_stored_object": True}
    stored_object = {"bytes": stored["ContentLength"], "etag": stored["ETag"].strip('"')}
    uploaded = {
        "bucket": BUCKET, "key": key, **streamed,
        "stored_bytes": stored_object["bytes"], "stored_etag": stored_object["etag"],
        "bytes_match_source": stored_object["bytes"] == source["bytes"],
        "seconds": round(time.perf_counter() - started, 2),
    }
    uploaded.update(readback(stored_object, s, key))
    uploaded["readback"] = ("FULL_READBACK_SHA256_AND_ETAG_MD5_MATCH"
                           if uploaded.pop("etag_matches_readback_md5")
                           else "FULL_READBACK_SHA256;ETAG_MD5_MISMATCH")

    index, requests = member_index(stored_object, s, key)
    index_raw = encoded(index)
    index_key = PREFIX + snapshot.replace("-", "") + "/member-index.json"
    s.put_object(Bucket=BUCKET, Key=index_key, Body=index_raw, IfNoneMatch="*")
    index_head = s.head_object(Bucket=BUCKET, Key=index_key)
    diff = difference(previous_entries, index)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": snapshot,
        "source": source,
        "identity": IDENTITY,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "archive": {**uploaded, "r2_member_index": {
            "key": index_key, "bytes": len(index_raw), "sha256": digest(index_raw),
            "readback": "HEAD_ETAG_MD5_MATCH"
            if index_head["ETag"].strip('"') == hashlib.md5(index_raw).hexdigest() else "MISMATCH",
            "members": len(index), "central_directory_range_requests": requests}},
        "previous_release": {
            "snapshot_date": base["source"]["snapshot_date"], "key": base["source"]["key"],
            "declared_sha256": base["source"]["declared_sha256"], "bytes": base["source"]["bytes"],
        },
        "difference_from_pinned_snapshot": diff,
        "filing_availability": "MEMBER_CONTENTS_NOT_YET_NORMALIZED_FOR_THIS_RELEASE",
        "transformed_dataset_hashes": {},
        "current_pointer_writes": 0,
        "engine_release_pins_this_source": False,
        "estimated_first_month_usd": ESTIMATED_FIRST_MONTH_USD,
        "note": (
            "Immutable research release. The September 24, 2026 snapshot, its engine release and "
            "its readiness release are untouched, and no serving pointer was promoted: an "
            "unmeasured release does not become current by being stored."),
    }
    receipt_raw = encoded(manifest)
    manifest_key = PREFIX + snapshot.replace("-", "") + "/manifest.json"
    s.put_object(Bucket=BUCKET, Key=manifest_key, Body=receipt_raw, IfNoneMatch="*")
    receipt_head = s.head_object(Bucket=BUCKET, Key=manifest_key)
    manifest["publication_receipt"] = {
        "manifest_key": manifest_key, "bytes": len(receipt_raw),
        "sha256": digest(receipt_raw),
        "readback": "HEAD_ETAG_MD5_MATCH"
        if receipt_head["ETag"].strip('"') == hashlib.md5(receipt_raw).hexdigest() else "MISMATCH",
    }
    (directory / "manifest.json").write_bytes(encoded(manifest) + b"\n")
    (directory / "member-index.json").write_bytes(index_raw + b"\n")
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
