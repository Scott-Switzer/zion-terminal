"""Publish a new immutable public-pilot policy; never touches production pointers."""

import hashlib
import json
from pathlib import Path

from reconcile_financial_inventory import client


def main():
    out = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-financial-foundation")
    raw = (out / "basin-pilot-policy.json").read_bytes()
    p = json.loads(raw)
    assert p["licensed_datasets_allowed"] is False and p["promotion_allowed"] is False
    digest = hashlib.sha256(raw).hexdigest()
    key = "gold/unified-foundation/policies/" + digest + ".json"
    s = client()
    try:
        s.put_object(
            Bucket="financial-system-datasets",
            Key=key,
            Body=raw,
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except s.exceptions.ClientError as e:
        if e.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
            raise
    stored = s.get_object(Bucket="financial-system-datasets", Key=key)["Body"].read(262145)
    assert stored == raw
    receipt = {
        "key": key,
        "sha256": digest,
        "namespace": p["namespace"],
        "rights": "PUBLIC_SEC_AND_OWNED_SYNTHETIC_ONLY",
        "production_pointer_changes": 0,
    }
    (out / "basin-policy-publication.json").write_text(json.dumps(receipt, indent=2))
    root = Path(__file__).parents[1]
    config = root / "deploy/datasets/wrangler.jsonc"
    c = json.loads(config.read_text())
    c["vars"]["BASIN_POLICY_KEY"] = key
    c["vars"]["BASIN_POLICY_SHA256"] = digest
    config.write_text(json.dumps(c, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
