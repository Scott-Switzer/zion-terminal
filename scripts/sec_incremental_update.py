"""Opt-in SEC companyfacts refresh into immutable, unpromoted candidate overlays."""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).parents[1] / "deploy/cloudflare-query/src"))
from sec_engine import encoded, normalize_sources

LIMIT = 24 * 1048576
PREFIX = "gold/sec-engine/update-candidates/"


def sha(body):
    return hashlib.sha256(body).hexdigest()


def bounded_read(path, limit=LIMIT):
    if path.stat().st_size > limit:
        raise ValueError("SOURCE_BYTE_LIMIT")
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("SOURCE_BYTE_LIMIT")
    return body


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def public(manifest):
    if (
        manifest.get("rights") != "PUBLIC_SEC_ONLY"
        or manifest.get("licensed_sources_allowed") is not False
    ):
        raise ValueError("PUBLIC_SEC_ONLY_REQUIRED")
    if not re.fullmatch(r"[a-f0-9]{64}", manifest.get("release_id", "")):
        raise ValueError("PREVIOUS_RELEASE_ID_REQUIRED")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class SecFetcher:
    """Sequential 2 requests/second maximum; caller must coordinate aggregate traffic."""

    def __init__(self, user_agent):
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", user_agent) or "\n" in user_agent:
            raise ValueError("IDENTIFYING_USER_AGENT_WITH_CONTACT_REQUIRED")
        self.user_agent = user_agent
        self.last_request = 0.0
        self.opener = build_opener(NoRedirect())

    def get(self, cik, kind="companyfacts", etag=None):
        if not re.fullmatch(r"\d{10}", cik) or kind not in ("companyfacts", "submissions"):
            raise ValueError("INVALID_SEC_RESOURCE")
        path = "api/xbrl/companyfacts/" if kind == "companyfacts" else "submissions/"
        url = "https://data.sec.gov/" + path + "CIK" + cik + ".json"
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if etag:
            if "\n" in etag or "\r" in etag:
                raise ValueError("INVALID_ETAG")
            headers["If-None-Match"] = etag
        for attempt in range(3):
            time.sleep(max(0, 0.5 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with self.opener.open(Request(url, headers=headers), timeout=30) as response:
                    if int(response.headers.get("Content-Length", 0)) > LIMIT:
                        raise ValueError("SOURCE_BYTE_LIMIT")
                    body = response.read(LIMIT + 1)
                    if len(body) > LIMIT:
                        raise ValueError("SOURCE_BYTE_LIMIT")
                    return body, {
                        "url": url,
                        "etag": response.headers.get("ETag"),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "http_status": response.status,
                    }
            except HTTPError as error:
                if error.code == 304:
                    return None, {"url": url, "etag": etag, "http_status": 304}
                if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
                # Bounded delay; no bypass of SEC access denials or alternate hosts.
                time.sleep(min(8, 2 ** (attempt + 1)))
        raise RuntimeError("UNREACHABLE")


def build_candidate(previous, previous_body, updates, fetched_at):
    public(previous)
    if json.loads(previous_body) != previous:
        raise ValueError("PREVIOUS_MANIFEST_BODY_MISMATCH")
    retrieval = instant(fetched_at)
    if retrieval > datetime.now(timezone.utc):
        raise ValueError("FUTURE_RETRIEVAL_TIME")
    if not 1 <= len(updates) <= 100:
        raise ValueError("UPDATE_COMPANY_BUDGET")
    registry_sha = previous.get("registry_sha256")
    if not re.fullmatch(r"[a-f0-9]{64}", registry_sha or ""):
        raise ValueError("REGISTRY_PIN_REQUIRED")
    changed = {}
    objects = {}
    for cik, item in sorted(updates.items()):
        if not re.fullmatch(r"\d{10}", cik):
            raise ValueError("INVALID_CIK")
        body, metadata, prior_raw = item
        if len(body) > LIMIT:
            raise ValueError("SOURCE_BYTE_LIMIT")
        company = json.loads(body, parse_float=Decimal)
        if not isinstance(company, dict) or not isinstance(company.get("facts"), dict):
            raise ValueError("COMPANYFACTS_SCHEMA_REQUIRED")
        source_cutoff = min(retrieval, instant(metadata.get("fetched_at", fetched_at)))
        if (
            company.get("licensed_sources_allowed")
            or company.get("rights", "PUBLIC_SEC_ONLY") != "PUBLIC_SEC_ONLY"
        ):
            raise ValueError("PUBLIC_SEC_ONLY_REQUIRED")
        digest = sha(body)
        normalized = normalize_sources(company, cik, digest)
        if prior_raw is not None and sha(prior_raw) == digest:
            continue
        old_ids = set()
        if prior_raw is not None:
            if len(prior_raw) > LIMIT:
                raise ValueError("SOURCE_BYTE_LIMIT")
            old = normalize_sources(json.loads(prior_raw, parse_float=Decimal), cik, sha(prior_raw))
            old_ids = {r["source_observation_id"] for r in old["observations"]}
        new_ids = {r["source_observation_id"] for r in normalized["observations"]}
        # A full current companyfacts snapshot replaces its predecessor. Never union old rows.
        unavailable = [
            r for r in normalized["observations"] if instant(r["available_at"]) > source_cutoff
        ]
        normalized["observations"] = [
            r for r in normalized["observations"] if instant(r["available_at"]) <= source_cutoff
        ]
        normalized["rejected_contexts"] = [
            r
            for r in normalized["rejected_contexts"]
            if instant(r["available_at"]) <= source_cutoff
        ]
        normalized.update(
            registry_sha256=registry_sha,
            source_lineage={
                "rights": "PUBLIC_SEC_ONLY",
                "member_sha256": digest,
                "retrieved_at": fetched_at,
                "source": metadata,
            },
            snapshot_replacement_policy="AUTHORITATIVE_FULL_SNAPSHOT_NO_UNION",
        )
        changed[cik] = {
            "member_sha256": digest,
            "raw_bytes": len(body),
            "removed_source_ids": sorted(old_ids - new_ids),
            "coverage_gaps": [
                {
                    "reason": "REMOVED_FROM_AUTHORITATIVE_SOURCE_SNAPSHOT",
                    "source_observation_id": sid,
                }
                for sid in sorted(old_ids - new_ids)
            ],
            "added_source_ids": sorted(new_ids - old_ids),
            "availability_excluded": len(unavailable),
            "maximum_as_of": source_cutoff.isoformat(),
            "deletion_check": "COMPARED" if prior_raw is not None else "NO_PRIOR_RAW",
            "coverage_limitations": [
                "Companyfacts covers standard entity-wide tags only",
                "Historical removed facts remain in prior release only",
            ],
        }
        objects["raw/" + cik + "/" + digest + ".json"] = body
        objects["companies/" + cik + ".json"] = encoded(normalized)
    manifest = {
        "schema_version": "sec-update-candidate-v1",
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "previous_release_id": previous["release_id"],
        "previous_manifest_sha256": sha(previous_body),
        "registry_sha256": registry_sha,
        "implementation_sha256": sha(
            (Path(__file__).parents[1] / "deploy/cloudflare-query/src/sec_engine.py").read_bytes()
        ),
        "registry_compatibility_gate": "REQUIRES_INDEPENDENT_PROMOTION_CHECK",
        "retrieved_at": fetched_at,
        "maximum_as_of": retrieval.isoformat(),
        "companies": changed,
        "promotion_status": "UNAPPROVED_CANDIDATE",
        "merge_policy": "UNCHANGED_CIK_INHERIT_PREVIOUS;CHANGED_CIK_REPLACE_FULL_SNAPSHOT",
        "production_pointer_writes": 0,
    }
    release = sha(encoded(manifest))
    manifest["release_id"] = release
    for name, body in list(objects.items()):
        if name.startswith("companies/"):
            value = json.loads(body)
            value["release_id"] = release
            objects[name] = encoded(value)
    manifest["objects"] = {
        name: {"key": PREFIX + release + "/" + name, "sha256": sha(body), "bytes": len(body)}
        for name, body in sorted(objects.items())
    }
    objects["manifest.json"] = encoded(manifest)
    return manifest, objects


def publish(client, manifest, objects):
    prefix = PREFIX + manifest["release_id"] + "/"
    for name, body in objects.items():
        key = prefix + name
        try:
            client.put_object(
                Bucket="financial-system-datasets",
                Key=key,
                Body=body,
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except client.exceptions.ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
        response = client.get_object(Bucket="financial-system-datasets", Key=key)
        try:
            actual = response["Body"].read(len(body) + 1)
        finally:
            response["Body"].close()
        if actual != body:
            raise ValueError("IMMUTABLE_OBJECT_CONFLICT")
    return {
        "release_id": manifest["release_id"],
        "objects_verified": len(objects),
        "production_pointer_writes": 0,
        "promotion_status": "UNAPPROVED_CANDIDATE",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-manifest", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fetched-at", help="Required fixture retrieval timestamp")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--cik", action="append", default=[])
    parser.add_argument("--user-agent")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    previous_body = bounded_read(args.previous_manifest, 64 * 1048576)
    previous = json.loads(previous_body)
    public(previous)
    args.input_dir.mkdir(parents=True, exist_ok=True)
    updates = {}
    fetcher = SecFetcher(args.user_agent or "") if args.fetch else None
    if args.fetch and (not args.cik or len(args.cik) > 100):
        parser.error("--fetch requires 1..100 --cik entries")
    paths = [args.input_dir / (cik + ".companyfacts.json") for cik in args.cik]
    if not args.fetch:
        paths = sorted(args.input_dir.glob("??????????.companyfacts.json"))
    if not 1 <= len(paths) <= 100:
        parser.error("requires 1..100 companyfacts inputs")
    for path in paths:
        cik = path.name.split(".")[0]
        prior_path = args.input_dir / (cik + ".previous.companyfacts.json")
        metadata_path = args.input_dir / (cik + ".http.json")
        metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
        if fetcher:
            body, metadata = fetcher.get(cik, etag=metadata.get("etag"))
            if body is None:
                continue
            old_body = bounded_read(path) if path.exists() else None
        else:
            if not args.fetched_at:
                parser.error("--input-dir fixtures require --fetched-at")
            body = bounded_read(path)
            old_body = bounded_read(prior_path) if prior_path.exists() else None
        updates[cik] = (body, metadata, old_body)
    if not updates:
        print(json.dumps({"status": "UNCHANGED", "production_pointer_writes": 0}))
        return
    fetched = args.fetched_at or datetime.now(timezone.utc).isoformat()
    manifest, objects = build_candidate(previous, previous_body, updates, fetched)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, body in objects.items():
        target = args.output_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != body:
            raise ValueError("LOCAL_IMMUTABLE_CONFLICT")
        target.write_bytes(body)
    receipt = {
        "release_id": manifest["release_id"],
        "changed_companies": len(manifest["companies"]),
        "promotion_status": "UNAPPROVED_CANDIDATE",
        "production_pointer_writes": 0,
    }
    if args.publish:
        from reconcile_financial_inventory import client

        receipt.update(publish(client(), manifest, objects))
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
