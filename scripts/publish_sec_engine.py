"""Publish hash-pinned SEC engine catalog; create-only public objects, no CURRENT writes."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from materialize_sec_engine import OUT
from reconcile_financial_inventory import client
from sec_engine import encoded
from sec_screener import build_index


def main():
    m = json.loads((OUT / "engine-manifest.json").read_text())
    entries = json.loads((OUT / "full-member-index.json").read_text())
    prefix = "gold/sec-engine/releases/" + m["release_id"] + "/"
    s = client()
    objects = {}

    def pin(name, body):
        key = prefix + name
        objects[key] = body
        return {"key": key, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}

    for cik, ref in m["companies"].items():
        raw = (OUT / (cik + ".history.json")).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
        ref["key"] = prefix + "companies/" + cik + ".json"
        objects[ref["key"]] = raw
    shards = {}
    directory = []
    for cik, e in entries.items():
        ref = m["companies"].get(cik)
        item = {"archive_entry": e, "label": ref["label"] if ref else None}
        if cik in m.get("quarantined", {}):
            item["quarantine"] = m["quarantined"][cik]
        if ref:
            item.update(
                materialized={k: ref[k] for k in ("key", "sha256", "bytes", "member_sha256")},
                coverage=ref["coverage"],
                policy=ref["policy"],
            )
        shards.setdefault(cik[-2:], {})[cik] = item
        directory.append(
            {
                "cik": cik,
                "label": item["label"],
                "status": "MATERIALIZED"
                if ref
                else "QUARANTINED"
                if item.get("quarantine")
                else "ELIGIBILITY_NOT_YET_VERIFIED",
                "quarantine_reason": item.get("quarantine", {}).get("reason"),
            }
        )
    root = {k: v for k, v in m.items() if k != "companies"}
    root["shards"] = {
        suffix: pin("catalog/" + suffix + ".json", encoded(rows))
        for suffix, rows in sorted(shards.items())
    }
    root["directory"] = pin("directory.json", encoded(sorted(directory, key=lambda x: x["cik"])))
    screening = encoded(build_index(m, OUT))
    (OUT / "screen-index.json").write_bytes(screening)
    root["screening_index"] = pin("screen-index.json", screening)
    root["registry"] = pin("registry.json", (OUT / "registry.json").read_bytes())
    root["materialized_companies"] = len(m["companies"])
    for name in ("sec_engine.py", "sec_screener.py", "temporal_core.py", "new_york_tz.py"):
        pin(
            "implementation/" + name,
            (Path(__file__).parents[1] / "deploy/cloudflare-query/src" / name).read_bytes(),
        )
    reference = pin("manifest.json", encoded(root))

    def write(item):
        key, body = item
        try:
            s.put_object(
                Bucket="financial-system-datasets",
                Key=key,
                Body=body,
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except s.exceptions.ClientError as ex:
            if ex.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
        r = s.get_object(Bucket="financial-system-datasets", Key=key)
        try:
            raw = r["Body"].read(len(body) + 1)
        finally:
            r["Body"].close()
        assert raw == body
        return len(body)

    with ThreadPoolExecutor(max_workers=4) as pool:
        total = sum(pool.map(write, objects.items()))
    receipt = {
        "release_id": m["release_id"],
        "root": reference,
        "objects": len(objects),
        "bytes": total,
        "catalog_issuers": len(entries),
        "materialized_companies": len(m["companies"]),
        "current_pointer_writes": 0,
        "licensed_sources": 0,
    }
    (OUT / "publication.json").write_bytes(encoded(receipt))
    (OUT / "serving-manifest.json").write_bytes(encoded(root))
    p = Path(__file__).parents[1] / "deploy/cloudflare-query/thin/wrangler.jsonc"
    cfg = json.loads(p.read_text())
    cfg["vars"].update(
        SEC_ENGINE_MANIFEST_KEY=reference["key"],
        SEC_ENGINE_MANIFEST_SHA256=reference["sha256"],
        SEC_ENGINE_ENABLED="true",
    )
    cfg["queues"] = {
        "producers": [{"binding": "SEC_ENGINE_QUEUE", "queue": "zion-sec-engine-jobs"}],
        "consumers": [
            {
                "queue": "zion-sec-engine-jobs",
                "max_batch_size": 1,
                "max_batch_timeout": 1,
                "max_retries": 2,
                "max_concurrency": 1,
            }
        ],
    }
    cfg["limits"] = {"cpu_ms": 30000}
    p.write_text(json.dumps(cfg, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
