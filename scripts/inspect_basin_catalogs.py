"""Metadata-only catalog registration census; never reads table records."""

import json

from publish_basin_public_pilot import ACCOUNT, OUT, token
from pyiceberg.catalog import load_catalog


def main():
    results = []
    for bucket in [
        "financial-system-datasets",
        "financial-data-wrds-lakehouse",
        "financial-data-wrds-private",
        "ppe-sec-intelligence-prod",
    ]:
        item = {"bucket": bucket, "tables": []}
        try:
            cat = load_catalog(
                "r2",
                type="rest",
                uri=f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{bucket}",
                warehouse=f"{ACCOUNT}_{bucket}",
                token=token(),
            )
            namespaces = cat.list_namespaces()
            for namespace in namespaces:
                for identifier in cat.list_tables(namespace):
                    t = cat.load_table(identifier)
                    snap = t.current_snapshot()
                    item["tables"].append(
                        {
                            "identifier": list(identifier),
                            "snapshot_id": str(snap.snapshot_id) if snap else None,
                            "records_declared": snap.summary.model_dump().get("total-records")
                            if snap
                            else None,
                            "bytes_declared": snap.summary.model_dump().get("total-files-size")
                            if snap
                            else None,
                        }
                    )
            item["status"] = "PASS_METADATA_CATALOG_ENUMERATION_ONLY"
        except Exception as e:
            item["status"] = "FAIL_CATALOG_NOT_VERIFIED"
            item["error_type"] = type(e).__name__
        results.append(item)
        print(
            json.dumps({"bucket": bucket, "status": item["status"], "tables": len(item["tables"])}),
            flush=True,
        )
    (OUT / "catalog-registration-census.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
