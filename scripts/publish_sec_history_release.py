"""Create-only publication of approved public SEC release objects; no CURRENT writes."""

import hashlib
import json
from pathlib import Path

from extract_sec_history_sources import OUT
from reconcile_financial_inventory import client


def main():
    manifest = json.loads((OUT / "history-manifest.json").read_text())
    prefix = "gold/sec-financial-history/releases/" + manifest["release_id"] + "/"
    assert manifest["rights"] == "PUBLIC_SEC_ONLY" and not manifest["licensed_sources_allowed"]
    objects = {
        ref["key"]: (OUT / (cik + ".history.json")).read_bytes()
        for cik, ref in manifest["companies"].items()
    }
    objects[prefix + "manifest.json"] = (OUT / "history-manifest.json").read_bytes()
    objects[prefix + "metric-registry.json"] = (OUT / "metric-registry.json").read_bytes()
    for name in ("sec_history.py", "temporal_core.py", "new_york_tz.py"):
        objects[prefix + "implementation/" + name] = (
            Path(__file__).parents[1] / "deploy/cloudflare-query/src" / name
        ).read_bytes()
    s = client()
    for key, body in objects.items():
        assert key.startswith(prefix) and "CURRENT" not in key
        try:
            s.put_object(
                Bucket="financial-system-datasets",
                Key=key,
                Body=body,
                ContentType="application/json",
                IfNoneMatch="*",
            )
        except s.exceptions.ClientError as e:
            if e.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
        stored = s.get_object(Bucket="financial-system-datasets", Key=key)["Body"].read(
            16 * 1048576 + 1
        )
        assert stored == body
    key = prefix + "manifest.json"
    sha = hashlib.sha256(objects[key]).hexdigest()
    receipt = {
        "key": key,
        "sha256": sha,
        "release_id": manifest["release_id"],
        "objects": len(objects),
        "production_pointer_changes": 0,
        "licensed_rows_migrated": 0,
    }
    (OUT / "history-publication.json").write_text(json.dumps(receipt, indent=2) + "\n")
    config = Path(__file__).parents[1] / "deploy/cloudflare-query/thin/wrangler.jsonc"
    cfg = json.loads(config.read_text())
    cfg["vars"]["SEC_HISTORY_MANIFEST_KEY"] = key
    cfg["vars"]["SEC_HISTORY_MANIFEST_SHA256"] = sha
    config.write_text(json.dumps(cfg, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
