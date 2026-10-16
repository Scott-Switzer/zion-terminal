"""Materialize the screening index that the private readiness API serves.

The record release under ``gold/sec-readiness/releases/<release>/`` holds one full evidence
record per issuer and is the authority: this script never rewrites it, and the manifest it
publishes names that release's manifest key, digest and part digests so a reader can verify the
bytes it screened.

Screenings are answered from a *derived* index instead of from the records, because "which
issuers are eligible for an operating DCF" must not decompress 21 parts of 20,326 records on
every interactive request. The index is deliberately redundant so that every query is bounded by
construction: one artifact per screening filter (method, flag, missing-input code, status) and
one per CIK prefix shard, each holding the same compact row. A request reads the manifest for the
counts, then the single artifact of the narrowest filter it was given, and applies the remaining
predicates in memory.

    python3 scripts/sec_readiness_index.py --local
    python3 scripts/sec_readiness_index.py --publish

Publication is create-only (``IfNoneMatch="*"``) and every object is read back by HEAD before the
receipt is written. The index id is the digest of everything the manifest asserts, so the same
records always produce the same id and a re-run is refused rather than silently re-published.
"""

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

BUCKET = "financial-system-datasets"
RECORD_PREFIX = "gold/sec-readiness/releases/"
INDEX_PREFIX = "gold/sec-readiness/index/"
RECORDS = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-readiness")
OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-readiness-index")
SCHEMA = "zion-readiness-screen-index-v1"
METHODS = ("operating_dcf", "fcfe", "apv", "bank_excess_return", "residual_income", "ddm",
           "comparables")
FLAGS = ("financial_history_available", "complete_equity_bridge", "dated_share_basis_verified",
         "any_implemented_method_eligible", "operating_dcf_eligible",
         "specialized_method_required")
QUALIFICATION = (
    "COMPANY_SIDE_INPUT_ELIGIBILITY;SCREENING_VIEW_OF_A_PINNED_RECORD_RELEASE;"
    "EVIDENCE_STATES_NOT_COLLAPSED;NO_ASSUMPTION_OR_DISCOUNT_RATE_INCLUDED;"
    "REVIEWED_ABSENCE_IS_A_VERIFIED_ZERO")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(client, key):
    body = client.get_object(Bucket=BUCKET, Key=key)
    try:
        return body["Body"].read()
    finally:
        body["Body"].close()


def records(release, client=None, local=None):
    """Yield each record with the part it came from, one part in memory at a time."""
    for part in release["parts"]:
        name = part["part"]
        if local is not None:
            raw = gzip.decompress((local / name).read_bytes())
        else:
            raw = gzip.decompress(read(client, RECORD_PREFIX + release["release_id"] + "/" + name))
        for member in raw.decode().splitlines():
            if member.strip():
                record = json.loads(member)
                record["part"] = name
                yield record


def row(record):
    """One compact screening row: the verdicts, the evidence states and where the evidence is.

    Nothing is collapsed here. A claim that was reviewed and found absent keeps its own state
    instead of contributing a zero to a share basis, and an issuer that could not be classified
    keeps that status rather than being dropped from the universe.
    """
    classified = record.get("status") == "CLASSIFIED"
    readiness = record.get("readiness") or {}
    methods = record.get("valuation", {}).get("methods", {}) if classified else {}
    claims = record.get("equity_claims", {}).get("claims", {}) if classified else {}
    shares = record.get("equity_claims", {}).get("shares", {}) if classified else {}
    return {
        "cik": record["cik"],
        "label": record.get("label"),
        "status": record.get("status"),
        "part": record.get("part"),
        "methods": sorted(name for name, value in methods.items() if value.get("eligible")),
        "missing": list(record.get("missing_inputs") or []),
        "flags": [name for name in FLAGS if readiness.get(name)],
        "share_basis": shares.get("basis"),
        "refused_claims": sorted(name for name, claim in claims.items()
                                 if claim.get("permitted_for_valuation") is False),
        # A count, not a list: the claims themselves are in the record this row points at, and
        # the count is what makes a reviewed absence distinguishable from an unknown here.
        "reviewed_absent_claims": sum(1 for claim in claims.values() if claim.get("verified_absent")),
    }


def filter_names(item):
    """Every filter this row belongs to; the union of these is the published index."""
    names = ["status-" + str(item["status"])]
    names += ["method-" + name for name in item["methods"]]
    names += ["flag-" + name for name in item["flags"]]
    names += ["missing-" + code for code in item["missing"]]
    return names


def build(release, client=None, local=None):
    """Read the record release once, writing each filter and CIK shard as it is needed."""
    artifacts = {}
    rows, buckets, shards = 0, {}, {}
    counts = {"methods": Counter(), "flags": Counter(), "missing": Counter(), "status": Counter()}

    def store(name, items):
        body = encoded(items) + b"\n"
        artifacts[name] = {"name": name, "bytes": len(body), "sha256": digest(body),
                           "issuers": len(items), "body": body}

    for record in records(release, client=client, local=local):
        item = row(record)
        rows += 1
        for name in filter_names(item):
            buckets.setdefault(name, []).append(item)
        # CIKs are zero-padded to ten digits, so a prefix shard would be one shard; the
        # numeric remainder is what actually spreads twenty thousand issuers over the index.
        shards.setdefault("%02d" % (int(item["cik"]) % 100), []).append(item)
        counts["methods"].update(item["methods"])
        counts["flags"].update(item["flags"])
        counts["missing"].update(item["missing"])
        counts["status"][str(item["status"])] += 1
    for name in sorted(buckets):
        store("buckets/" + name + ".json", buckets[name])
    for prefix in sorted(shards):
        store("cik/" + prefix + ".json", shards[prefix])
    return artifacts, rows, {kind: dict(sorted(value.items(), key=lambda i: (-i[1], i[0])))
                             for kind, value in counts.items()}


def record_reference(release, client=None, local=None):
    """The record manifest key, digest and part digests, so the screened bytes can be verified."""
    if local is not None:
        manifest = (local / "manifest.json").read_bytes()
    else:
        manifest = read(client, RECORD_PREFIX + release["release_id"] + "/manifest.json")
    if json.loads(manifest)["release_id"] != release["release_id"]:
        raise ValueError("RECORD_MANIFEST_DOES_NOT_NAME_ITS_RELEASE")
    return {
        "key": RECORD_PREFIX + release["release_id"] + "/manifest.json",
        "sha256": digest(manifest),
        "bytes": len(manifest),
        "parts": [{"name": part["part"], "bytes": part["bytes"], "sha256": part["sha256"]}
                  for part in release["parts"]],
    }


def summary_for(manifest):
    """The already-materialized summary: what a screening question is asked against."""
    return {
        "schema_version": SCHEMA,
        "index_id": manifest["index_id"],
        "release_id": manifest["record_release_id"],
        "index_prefix": INDEX_PREFIX + manifest["index_id"] + "/",
        "record_manifest": manifest["record_manifest"],
        "archive_cutoff": manifest["archive_cutoff"],
        "availability_cutoff": manifest["availability_cutoff"],
        "issuers": manifest["issuers"],
        "counts": manifest["counts"],
        "filters": {name: {"key": "buckets/" + name + ".json",
                           "bytes": artifact["bytes"], "issuers": artifact["issuers"],
                           "sha256": artifact["sha256"]}
                    for name, artifact in sorted(manifest["artifacts"].items())
                    if name.startswith("buckets/")},
        "cik_shards": sorted(name for name in manifest["artifacts"] if name.startswith("cik/")),
        "qualification": manifest["qualification"],
        "evidence_states": ["REPORTED", "REVIEWED_ABSENT", "UNAVAILABLE", "CONFLICTING"],
    }


def manifest_for(release, artifacts, rows, counts, reference):
    """The manifest. Its id is the digest of exactly what it asserts, and nothing else."""
    listing = {name: {"bytes": entry["bytes"], "sha256": entry["sha256"],
                      "issuers": entry["issuers"]}
               for name, entry in sorted(artifacts.items())}
    payload = {
        "schema_version": SCHEMA,
        "record_release_id": release["release_id"],
        "record_manifest": reference,
        "archive_cutoff": release.get("archive_cutoff"),
        "availability_cutoff": (release.get("archive") or {}).get("availability_cutoff"),
        "engine_release_id": release.get("engine_release_id"),
        "claims_contract_version": release.get("claims_contract_version"),
        "issuers": rows,
        "counts": counts,
        "artifacts": listing,
        "qualification": QUALIFICATION,
    }
    return payload | {"index_id": digest(encoded(payload))}


def publish(manifest, artifacts, client, out):
    """Upload the index under its id, create-only, reading every object back over HEAD."""
    prefix = INDEX_PREFIX + manifest["index_id"] + "/"
    bodies = {name: entry["body"] for name, entry in artifacts.items()}
    bodies["summary.json"] = encoded(summary_for(manifest)) + b"\n"
    bodies["manifest.json"] = encoded(manifest) + b"\n"
    objects = []
    for name in ["manifest.json"] + sorted(artifacts) + ["summary.json"]:
        raw = bodies[name]
        key = prefix + name
        client.put_object(Bucket=BUCKET, Key=key, Body=raw, IfNoneMatch="*")
        head = client.head_object(Bucket=BUCKET, Key=key)
        etag = head["ETag"].strip('"')
        if etag != hashlib.md5(raw).hexdigest():
            raise ValueError("PUBLICATION_READBACK_ETAG_MISMATCH:" + key)
        declared = manifest["artifacts"].get(name, {})
        if declared and declared["sha256"] != digest(raw):
            raise ValueError("INDEX_ARTIFACT_CHANGED_AFTER_MANIFEST:" + name)
        objects.append({"key": key, "bytes": len(raw), "sha256": digest(raw),
                        "issuers": declared.get("issuers"),
                        "readback": "HEAD_ETAG_MD5_MATCH", "etag": etag})
    (out / "publication.json").write_bytes(encoded({
        "schema_version": SCHEMA, "index_id": manifest["index_id"], "prefix": prefix,
        "record_release_untouched": True, "current_pointer_writes": 0,
        "objects": objects, "published_utc": datetime.now(timezone.utc).isoformat(),
    }) + b"\n")
    return objects


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", help="record release id, default the newest published one")
    parser.add_argument("--local", action="store_true",
                        help="index the recorded local release instead of the bucket copy")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    from sec_readiness_query import latest_release, read_manifest
    from reconcile_financial_inventory import client as r2
    # Reading the records locally is a convenience; publishing always writes to the bucket, so a
    # `--local --publish` run indexes the recorded copy and uploads it under the same digest.
    client = r2() if (args.publish or not args.local) else None
    if args.local:
        candidates = sorted(RECORDS.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise SystemExit("no recorded local readiness release to index")
        local = candidates[-1].parent
        release = json.loads((local / "manifest.json").read_text())
    else:
        local = None
        release = read_manifest(client, args.release) if args.release else latest_release(client)

    artifacts, rows, counts = build(release, client=client, local=local)
    manifest = manifest_for(release, artifacts, rows, counts,
                            record_reference(release, client=client, local=local))
    out = args.out / manifest["index_id"]
    out.mkdir(parents=True, exist_ok=True)
    for name, entry in artifacts.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(entry["body"])
    (out / "manifest.json").write_bytes(encoded(manifest) + b"\n")
    (out / "summary.json").write_bytes(encoded(summary_for(manifest)) + b"\n")
    receipt = {
        "index_id": manifest["index_id"], "record_release_id": release["release_id"],
        "issuers": rows, "artifacts": len(artifacts) + 2,
        "bytes": sum(entry["bytes"] for entry in artifacts.values()),
        "local": str(out), "counts": counts, "published": False,
    }
    if args.publish:
        receipt["published"] = True
        receipt["objects"] = len(publish(manifest, artifacts, client, out))
        receipt["prefix"] = INDEX_PREFIX + manifest["index_id"] + "/"
    else:
        (out / "publication.json").write_bytes(encoded(receipt) + b"\n")
    print(json.dumps({key: value for key, value in receipt.items() if key != "counts"},
                     indent=1))
    print(json.dumps({"flag_counts": counts["flags"], "status_counts": counts["status"]},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
