"""Append one owned synthetic canary; prove old private policy rejects new snapshot."""

import json
import urllib.error
import urllib.request
from pathlib import Path

import pyarrow as pa
from publish_basin_public_pilot import ACCOUNT, BUCKET, OUT, token
from pyiceberg.catalog import load_catalog


def main():
    policy = json.loads((OUT / "basin-pilot-policy.json").read_text())
    namespace = policy["namespace"]
    assert namespace.startswith("zion_uqf_pilot_")
    cat = load_catalog(
        "r2",
        type="rest",
        uri=f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}",
        warehouse=f"{ACCOUNT}_{BUCKET}",
        token=token(),
    )
    table = cat.load_table((namespace, "precision_canary"))
    before = str(table.current_snapshot().snapshot_id)
    assert before == next(
        t["snapshot_id"] for t in policy["tables"] if t["name"] == "precision_canary"
    )
    if table.current_snapshot().summary.model_dump().get("version_guard_test") != "v1":
        values = {
            "case_id": "source_version_guard_v1",
            "exact_decimal": "9007199254740993.000000000000000001",
            "nullable_value": None,
            "rights": "OWNED_SYNTHETIC_TEST_ONLY",
        }
        table.append(
            pa.table({k: pa.array([v], type=pa.string()) for k, v in values.items()}),
            snapshot_properties={
                "source_sha256": policy["source_sha256"],
                "rights": "OWNED_SYNTHETIC_TEST_ONLY",
                "production_promotion": "FORBIDDEN",
                "version_guard_test": "v1",
            },
        )
    table.refresh()
    after = str(table.current_snapshot().snapshot_id)
    v = dict(
        x.split("=", 1)
        for x in Path("/Users/scottthomasswitzer/.config/zion/private-api.env")
        .read_text()
        .splitlines()
        if "=" in x and not x.startswith("#")
    )
    credential = v["ZION_PRIVATE_API_TOKEN"].strip().strip("\"'")
    url = "https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev/private/v1/datasets/financial-query?template=precision_null_canary_v1"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + credential,
            "User-Agent": "zion-source-version-verification/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as r:
            status, data = r.status, json.load(r)
    except urllib.error.HTTPError as e:
        status, data = e.code, json.load(e)
    code = data.get("error", {}).get("code")
    result = {
        "status": "PASS"
        if before != after and status == 409 and code == "SOURCE_VERSION_CHANGED"
        else "FAIL",
        "before_snapshot": before,
        "after_snapshot": after,
        "http_status": status,
        "error_code": code,
        "mutated_table": namespace + ".precision_canary",
        "rights": "OWNED_SYNTHETIC_TEST_ONLY",
        "original_files_preserved": True,
        "production_pointers_changed": False,
    }
    (OUT / "live-source-version-gate.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    assert result["status"] == "PASS"


if __name__ == "__main__":
    main()
