"""Screen the published issuer-readiness release.

The readiness dataset is one record per issuer and method, in immutable gzipped parts under
`gold/sec-readiness/releases/<release>/`. This answers the screening questions the research
workflow needs against that release, reading the manifest and only the parts it has to:

    python3 scripts/sec_readiness_query.py --method operating_dcf
    python3 scripts/sec_readiness_query.py --bridge --shares --limit 20
    python3 scripts/sec_readiness_query.py --missing MISSING_CASH_FLOW_OR_CAPEX
    python3 scripts/sec_readiness_query.py --cik 0000320193
    python3 scripts/sec_readiness_query.py --summary

The same filters are the wire contract for the private API route: the server keeps a
materialized index per method and flag so an interactive request never scans the archive, and
returns the same fields this prints (`cik`, `label`, eligible methods, missing-input codes,
share basis, source release, evidence cutoff). Until that route exists, this is the queryable
form; it is deliberately not a second terminal frontend.
"""

import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PREFIX = "gold/sec-readiness/releases/"
BUCKET = "financial-system-datasets"
LOCAL = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-readiness")
FLAGS = ("financial_history_available", "complete_equity_bridge", "dated_share_basis_verified",
         "any_implemented_method_eligible", "operating_dcf_eligible", "specialized_method_required")


def latest_release(client=None, bucket=BUCKET):
    """The newest published readiness release, by its own manifest."""
    client = client or _client()
    listing = client.list_objects_v2(Bucket=bucket, Prefix=PREFIX, Delimiter="/")
    prefixes = [entry["Prefix"] for entry in listing.get("CommonPrefixes", [])]
    if not prefixes:
        raise SystemExit("no published readiness release under " + PREFIX)
    manifests = []
    for prefix in prefixes:
        head = client.get_object(Bucket=bucket, Key=prefix + "manifest.json")
        try:
            manifests.append(json.loads(head["Body"].read()))
        finally:
            head["Body"].close()
    return max(manifests, key=lambda m: (m["issuers"], m["release_id"]))


def _client():
    from reconcile_financial_inventory import client
    return client()


def read_manifest(client, release_id, bucket=BUCKET):
    """One named release's manifest, the only object that is fetched unconditionally."""
    body = client.get_object(Bucket=bucket, Key=PREFIX + release_id + "/manifest.json")
    try:
        return json.loads(body["Body"].read())
    finally:
        body["Body"].close()


def records(release, client=None, bucket=BUCKET, limit=None):
    """Stream the release parts, yielding one record at a time."""
    client = client or _client()
    for part in release["parts"]:
        body = client.get_object(Bucket=bucket, Key=PREFIX + release["release_id"] + "/"
                                 + part["part"])
        try:
            raw = body["Body"].read()
        finally:
            body["Body"].close()
        for line in gzip.decompress(raw).decode().splitlines():
            if line.strip():
                yield json.loads(line)
                limit = None if limit is None else limit - 1
                if limit == 0:
                    return


def matching(record, args):
    """A record matches when every supplied filter matches, so filters AND together."""
    if args.cik and record.get("cik") not in args.cik:
        return False
    if record.get("status") != "CLASSIFIED":
        # An issuer that could not be classified is still the answer to a question about that
        # issuer -- and only to that question, because every other filter needs a verdict.
        if not args.cik:
            return False
        return not (args.method or args.missing or args.any_method or args.financial
                    or args.conflicting or args.shares_absent
                    or any(getattr(args, flag, False) for flag in FLAGS))
    if args.method:
        methods = record["valuation"]["methods"]
        if args.method not in methods or not methods[args.method]["eligible"]:
            return False
    if args.missing and args.missing not in record["missing_inputs"]:
        return False
    if args.any_method and not record["readiness"]["any_implemented_method_eligible"]:
        return False
    if args.financial and not record["readiness"]["specialized_method_required"]:
        return False
    if args.conflicting and not any(c["permitted_for_valuation"] is False
                                    for c in record["equity_claims"]["claims"].values()):
        return False
    for flag in FLAGS:
        if getattr(args, flag, False) and not record["readiness"][flag]:
            return False
    if args.shares_absent and record["readiness"]["dated_share_basis_verified"]:
        return False
    return True


def line(record):
    methods = [name for name, value in sorted(record["valuation"]["methods"].items())
               if value["eligible"]]
    return "%-12s %-34s %s%s" % (
        record["cik"], (record.get("label") or "")[:34], ",".join(methods) or "-",
        ("  missing=" + ",".join(record["missing_inputs"][:3])) if record["missing_inputs"] else "")


def add_filters(parser):
    parser.add_argument("--method", choices=("operating_dcf", "fcfe", "apv", "bank_excess_return",
                                             "residual_income", "ddm", "comparables"))
    parser.add_argument("--missing",
                        help="a missing-input code, e.g. MISSING_DATED_OUTSTANDING_SHARES")
    parser.add_argument("--cik", action="append")
    parser.add_argument("--any-method", action="store_true", dest="any_method")
    parser.add_argument("--financial", action="store_true",
                        help="issuers requiring a specialized financial method")
    parser.add_argument("--conflicting", action="store_true")
    parser.add_argument("--shares-absent", action="store_true", dest="shares_absent")
    parser.add_argument("--bridge", action="store_true", dest="complete_equity_bridge")
    parser.add_argument("--history", action="store_true", dest="financial_history_available")
    parser.add_argument("--limit", type=int, default=25)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_filters(parser)
    parser.add_argument("--release", help="readiness release id, default the newest published one")
    parser.add_argument("--local", action="store_true", help="read the recorded local artifacts")
    parser.add_argument("--summary", action="store_true", help="print the release summary only")
    args = parser.parse_args()
    client = None if args.local else _client()
    if args.local:
        candidates = sorted(LOCAL.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise SystemExit("no local readiness release in " + str(LOCAL))
        release = json.loads(candidates[-1].read_text())
    elif args.release:
        release = read_manifest(client, args.release)
    else:
        release = latest_release(client)
    if args.summary:
        head = client.get_object(Bucket=BUCKET, Key=PREFIX + release["release_id"]
                                 + "/summary.json") if client else None
        if head:
            try:
                print(json.dumps(json.loads(head["Body"].read()), indent=1))
            finally:
                head["Body"].close()
        else:
            print(json.dumps({"release_id": release["release_id"], "issuers": release["issuers"],
                              "parts": len(release["parts"])}, indent=1))
        return
    print("# release %s  issuers %d  cutoff %s" % (release["release_id"], release["issuers"],
                                                   release.get("archive_cutoff")))
    shown, by_method, by_missing = 0, Counter(), Counter()
    for record in records(release, client):
        if not matching(record, args):
            continue
        shown += 1
        for name, value in record["valuation"]["methods"].items():
            if value["eligible"]:
                by_method[name] += 1
        for code in record["missing_inputs"]:
            by_missing[code] += 1
        if shown <= args.limit:
            print(line(record))
    print("\nmatched %d issuers" % shown)
    if shown:
        print("eligible by method: " + json.dumps(dict(sorted(by_method.items(),
                                                              key=lambda i: -i[1]))))
        print("top missing inputs among matches: "
              + json.dumps(dict(sorted(by_missing.items(), key=lambda i: -i[1])[:6])))


if __name__ == "__main__":
    main()
