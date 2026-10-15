"""Publish the versioned companion SEC Company Facts source for the claims route.

Why a companion release exists
------------------------------
A member of the SEC engine release is a *normalized projection*: the engine keeps only the
concepts its published metric registry covers, so claims extraction cannot read leases,
preferred stock, noncontrolling interests, marketable securities, share counts or equity
totals from it. The raw Company Facts member is still available in the pinned public SEC
bulk archive the engine was built from, and the engine catalog entry records the digest it
verified for that member (``materialized.member_sha256``).

This script extracts exactly those member bytes from the same pinned archive, re-verifies
each byte against the zip entry's CRC32 and recorded size, re-verifies each digest against
the engine release's own pin, and publishes them as an immutable companion release. Nothing
immutable is rewritten, nothing is downloaded from SEC, and the engine release stays as it
was published.

Source chain per member (all recorded in the published manifest and in every claims
response):

    bulk archive (etag + declared sha256 + snapshot date)
      -> zip member (name, offset, compressed/uncompressed bytes, crc32)
      -> member sha256  ==  engine catalog materialized.member_sha256

I/O uses the repository's existing rclone R2 remote so the script needs no extra
dependency: reads stream with ranged reads, and writes use --immutable so an existing
object can never be overwritten.

    python3 scripts/publish_sec_companyfacts.py --ciks 0000320193,0000789019,0001652044 --dry-run
    python3 scripts/publish_sec_companyfacts.py --ciks ... --publish
    python3 scripts/publish_sec_companyfacts.py --verify-remote
"""

import argparse
import json
import hashlib
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEV_CONFIG = REPO / "deploy/cloudflare-query/thin/wrangler.dev.jsonc"
LOCAL_MEMBERS = Path(
    "/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale"
)
SCHEMA_VERSION = "sec-companyfacts-source-v1"
EXTRACTION_VERSION = "sec-companyfacts-member-v1"
SCOPE = "CONSOLIDATED_PUBLIC_SEC_COMPANY_FACTS"
PREFIX = "gold/sec-companyfacts/releases/"
WORKSPACE_BUCKET = "financial-system-datasets"
SOURCE_BUCKET = "financial-data-wrds-private"
MAX_MEMBER = 16 * 1048576
DEFAULT_CIKS = ("0000320193", "0000789019", "0001652044")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def jsonc(path):
    """Minimal comment-tolerant JSON reader for the wrangler dev config."""
    text = path.read_text()
    out, i, in_string, escaped = [], 0, False, False
    while i < len(text):
        char = text[i]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue
        if text.startswith("//", i):
            i = text.find("\n", i)
            if i == -1:
                break
            continue
        out.append(char)
        i += 1
    return json.loads("".join(out))


def run(args, *, binary=False):
    result = subprocess.run(args, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args[:3])} failed ({result.returncode}): "
            + result.stderr.decode(errors="replace").strip()[:400]
        )
    return result.stdout if binary else result.stdout.decode()


def read_object(bucket, key, *, offset=None, count=None):
    args = ["rclone", "cat", f"r2:{bucket}/{key}"]
    if offset is not None:
        args += ["--offset", str(offset)]
    if count is not None:
        args += ["--count", str(count)]
    return run(args, binary=True)


def read_json(bucket, key, *, expect_sha256=None):
    raw = read_object(bucket, key)
    if expect_sha256 and sha(raw) != expect_sha256:
        raise RuntimeError(f"{key}: sha256 mismatch (pinned object changed)")
    return json.loads(raw), raw


def extract_member(source, entry):
    """Re-extract one zip member from the pinned bulk archive with ranged reads only."""
    if entry["compression"] != 8 or entry["flags"] & 1:
        raise RuntimeError("MEMBER_BUDGET_OR_FORMAT")
    header = read_object(source["bucket"], source["key"], offset=entry["offset"], count=30)
    fields = struct.unpack("<4s5H3I2H", header)
    if fields[0] != b"PK\x03\x04" or fields[2] != entry["flags"] or fields[3] != entry["compression"]:
        raise RuntimeError("ZIP_HEADER_MISMATCH")
    name = read_object(
        source["bucket"], source["key"], offset=entry["offset"] + 30, count=fields[-2]
    ).decode()
    if name != entry["name"]:
        raise RuntimeError(f"ZIP_MEMBER_IDENTITY: {name!r}")
    body = read_object(
        source["bucket"],
        source["key"],
        offset=entry["offset"] + 30 + fields[-2] + fields[-1],
        count=entry["compressed_bytes"],
    )
    decoder = zlib.decompressobj(-15)
    raw = decoder.decompress(body, MAX_MEMBER + 1)
    if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        raise RuntimeError("ZIP_STREAM_INCOMPLETE")
    if len(raw) != entry["uncompressed_bytes"] or zlib.crc32(raw) != entry["crc"]:
        raise RuntimeError("ZIP_CRC_SIZE_MISMATCH")
    return raw


def load_catalog(engine):
    """CIK -> engine catalog entry, read from the pinned catalog shards only."""
    entries = {}
    shards = {}
    for suffix, ref in engine["shards"].items():
        shards[suffix] = read_json(WORKSPACE_BUCKET, ref["key"], expect_sha256=ref["sha256"])[0]
    return shards


def build_member(cik, engine, catalog, *, verify_local=True):
    entry = catalog[cik[-2:]][cik]
    pin = entry["materialized"]
    raw = extract_member(engine["source"], entry["archive_entry"])
    digest = sha(raw)
    if digest != pin["member_sha256"]:
        raise RuntimeError(
            f"{cik}: extracted member sha256 {digest} != engine pin {pin['member_sha256']}"
        )
    local = LOCAL_MEMBERS / f"{cik}.companyfacts.json"
    local_state = "ABSENT"
    if verify_local and local.exists():
        local_raw = local.read_bytes()
        local_state = "IDENTICAL" if local_raw == raw else "DIFFERS"
        if local_state == "DIFFERS":
            raise RuntimeError(f"{cik}: local extracted copy differs from the archive member")
    data = json.loads(raw)
    if str(data.get("cik")).zfill(10) != cik or not isinstance(data.get("facts"), dict):
        raise RuntimeError(f"{cik}: member is not a Company Facts document for this CIK")
    return raw, {
        "sha256": digest,
        "bytes": len(raw),
        "engine_member_sha256": pin["member_sha256"],
        "engine_release_id": pin.get("source_release_id"),
        "engine_member_key": pin.get("key"),
        "archive_entry": entry["archive_entry"],
        "label": entry.get("label"),
        "entity_name": data.get("entityName"),
        "local_extraction": local_state,
    }


# Local verification notes belong to the run, not to the immutable release: the manifest
# must be a pure function of the pinned source, the engine release and the member set, so
# that republishing the same release is idempotent byte for byte.
LOCAL_ONLY = ("local_extraction",)


def index_identity(engine, members):
    return encoded(
        {
            "schema_version": SCHEMA_VERSION,
            "extraction_version": EXTRACTION_VERSION,
            "scope": SCOPE,
            "source": engine["source"],
            "engine_release_id": engine["release_id"],
            "source_ceiling_date": engine["source"]["snapshot_date"],
            "engine_member_pins": {
                cik: m["engine_member_sha256"] for cik, m in sorted(members.items())
            },
            "members": {
                cik: {
                    "sha256": m["sha256"],
                    "bytes": m["bytes"],
                    "archive_entry": m["archive_entry"],
                }
                for cik, m in sorted(members.items())
            },
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciks", default=",".join(DEFAULT_CIKS))
    parser.add_argument("--engine-manifest-key")
    parser.add_argument("--engine-manifest-sha256")
    parser.add_argument("--out", type=Path, default=Path("/tmp/sec-companyfacts-release"))
    parser.add_argument("--publish", action="store_true", help="upload create-only to R2")
    parser.add_argument("--dry-run", action="store_true", help="verify everything, write nothing")
    parser.add_argument("--verify-remote", action="store_true", help="re-read and re-hash the release")
    parser.add_argument("--skip-local-compare", action="store_true")
    args = parser.parse_args()
    if not (args.publish or args.dry_run or args.verify_remote):
        parser.error("choose --dry-run, --publish or --verify-remote")

    config = jsonc(DEV_CONFIG)["vars"]
    key = args.engine_manifest_key or config["SEC_ENGINE_MANIFEST_KEY"]
    digest = args.engine_manifest_sha256 or config["SEC_ENGINE_MANIFEST_SHA256"]
    engine, _ = read_json(WORKSPACE_BUCKET, key, expect_sha256=digest)
    ciks = [c.strip() for c in args.ciks.split(",") if c.strip()]
    if any(not re.fullmatch(r"\d{10}", cik) for cik in ciks):
        raise SystemExit("ciks must be 10-digit zero-padded CIKs")

    catalog = load_catalog(engine)
    members = {}
    for cik in ciks:
        raw, ref = build_member(
            cik, engine, catalog, verify_local=not args.skip_local_compare
        )
        members[cik] = ref
        print(
            json.dumps(
                {
                    "cik": cik,
                    "label": ref["label"],
                    "bytes": ref["bytes"],
                    "member_sha256": ref["sha256"],
                    "engine_pin_matches": ref["sha256"] == ref["engine_member_sha256"],
                    "local_extraction": ref["local_extraction"],
                    "crc32_verified": True,
                }
            ),
            flush=True,
        )
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{cik}.companyfacts.json").write_bytes(raw)

    index_sha256 = sha(index_identity(engine, members))
    release_id = index_sha256
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "index_sha256": index_sha256,
        "extraction_version": EXTRACTION_VERSION,
        "scope": SCOPE,
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "source": engine["source"],
        "engine_releases": {
            "manifest": engine["release_id"],
            "raw_member_pins": sorted(
                {m["engine_release_id"] for m in members.values() if m["engine_release_id"]}
            ),
        },
        "date_from": engine["date_from"],
        "date_to": engine["date_to"],
        "maximum_as_of": engine["maximum_as_of"],
        # The snapshot is a date, not an instant: the archive holds everything the bulk
        # export published on that day, so the day itself is inside the release.
        "maximum_as_of_date": engine["source"]["snapshot_date"],
        "member_count": len(members),
        "members": {
            cik: {
                "key": PREFIX + release_id + "/companies/" + cik + ".json",
                **{k: v for k, v in ref.items() if k not in LOCAL_ONLY},
            }
            for cik, ref in sorted(members.items())
        },
        "previous_releases": {},
        "policy": (
            "IMMUTABLE_COMPANION_SOURCE;DUAL_RELEASE_PINNING;RAW_ARCHIVE_MEMBER_BYTES;"
            "NO_REQUEST_TIME_FETCH;NO_CALLER_SUPPLIED_KEYS"
        ),
        "reconciliation": (
            "Every companion member digest equals the digest the SEC engine release pinned "
            "for the same raw archive member."
        ),
    }
    manifest_raw = encoded(manifest)
    (args.out / "manifest.json").write_bytes(manifest_raw)
    receipt = {
        "release_id": release_id,
        "manifest_sha256": sha(manifest_raw),
        "members": len(members),
        "member_bytes": sum(m["bytes"] for m in members.values()),
        "source_archive_etag": engine["source"]["etag"],
        "source_archive": engine["source"]["key"],
        "engine_release_id": engine["release_id"],
        "published": False,
        "local_extraction": {
            cik: ref["local_extraction"] for cik, ref in sorted(members.items())
        },
    }

    if args.publish:
        for cik in members:
            member_key = PREFIX + release_id + "/companies/" + cik + ".json"
            run(
                [
                    "rclone", "copyto",
                    str(args.out / f"{cik}.companyfacts.json"),
                    f"r2:{WORKSPACE_BUCKET}/{member_key}",
                    "--immutable",
                ]
            )
        run(
            [
                "rclone", "copyto",
                str(args.out / "manifest.json"),
                f"r2:{WORKSPACE_BUCKET}/{PREFIX}{release_id}/manifest.json",
                "--immutable",
            ]
        )
        receipt["published"] = True

    if args.verify_remote or args.publish:
        remote_manifest_raw = read_object(
            WORKSPACE_BUCKET, f"{PREFIX}{release_id}/manifest.json"
        )
        if sha(remote_manifest_raw) != receipt["manifest_sha256"]:
            raise RuntimeError("remote manifest differs from the published bytes")
        remote = json.loads(remote_manifest_raw)
        for cik, ref in remote["members"].items():
            raw = read_object(WORKSPACE_BUCKET, ref["key"])
            if sha(raw) != ref["sha256"] or sha(raw) != ref["engine_member_sha256"]:
                raise RuntimeError(f"{cik}: remote member digest does not match its pin")
        receipt["remote_verified_members"] = len(remote["members"])

    receipt["manifest_key"] = PREFIX + release_id + "/manifest.json"
    print(json.dumps(receipt, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
