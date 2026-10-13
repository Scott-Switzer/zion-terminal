"""Stream metadata-only inventory for remaining stores; no source bodies are read."""

import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from reconcile_financial_inventory import client

OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-financial-foundation")


def scan(bucket):
    s = client()
    cursor = None
    count = 0
    size = 0
    formats = Counter()
    layers = Counter()
    digest = hashlib.sha256()
    pages = 0
    target = OUT / (bucket + "-metadata-summary.json")
    if target.exists():
        return json.loads(target.read_text())
    while True:
        args = {"Bucket": bucket, "MaxKeys": 1000}
        if cursor:
            args["ContinuationToken"] = cursor
        page = s.list_objects_v2(**args)
        pages += 1
        for o in page.get("Contents", []):
            count += 1
            size += o["Size"]
            key = o["Key"]
            layers[key.split("/")[0]] += 1
            formats[Path(key).suffix.lower() or "NO_EXTENSION"] += 1
            digest.update(
                json.dumps([key, o["Size"], o["ETag"]], separators=(",", ":")).encode() + b"\n"
            )
        if pages % 50 == 0:
            print(
                json.dumps(
                    {
                        "phase": "other_store_metadata",
                        "bucket": bucket,
                        "pages": pages,
                        "objects": count,
                    }
                ),
                flush=True,
            )
        cursor = page.get("NextContinuationToken")
        if not page.get("IsTruncated"):
            break
    result = {
        "bucket": bucket,
        "objects": count,
        "bytes": size,
        "formats": dict(formats),
        "layers": dict(layers),
        "ordered_metadata_sha256": digest.hexdigest(),
        "scope": "METADATA_CENSUS_NOT_TRANSACTIONAL_SNAPSHOT",
        "queryability": "FOOTER_AND_FULL_ROW_DECODING_NOT_CENSUSED",
        "source_rows_read": 0,
    }
    target.write_text(json.dumps(result, indent=2))
    target.chmod(0o600)
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(
            pool.map(
                scan,
                [
                    "financial-system-datasets",
                    "financial-data-wrds-lakehouse",
                    "ppe-sec-intelligence-prod",
                    "financial-data-wrds-private",
                ],
            )
        )
    p = OUT / "other-stores-metadata-summary.json"
    p.write_text(json.dumps(rows, indent=2))
    p.chmod(0o600)
