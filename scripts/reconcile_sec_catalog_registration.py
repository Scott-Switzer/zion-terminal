"""Verify management and REST catalog responses; never register licensed archives."""

import json
import urllib.error
import urllib.request

from extract_sec_history_sources import OUT
from publish_basin_public_pilot import ACCOUNT, token


def request(url, credential):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + credential,
            "User-Agent": "zion-catalog-reconciliation/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            data = json.load(e)
        except ValueError:
            data = {}
        return e.code, data


def main():
    results = []
    for bucket in [
        "financial-system-datasets",
        "financial-data-wrds-lakehouse",
        "financial-data-wrds-private",
        "ppe-sec-intelligence-prod",
    ]:
        status, body = request(
            f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/r2-catalog/{bucket}", token()
        )
        rest_status, rest = request(
            f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{bucket}/v1/config?warehouse={ACCOUNT}_{bucket}",
            token(),
        )
        state = (
            "REGISTERED_CATALOG_ACTIVE"
            if status == 200
            and body.get("result", {}).get("status") == "active"
            and rest_status == 200
            else "CATALOG_NOT_FOUND_UNDER_AVAILABLE_CREDENTIAL"
            if status == 404 and rest_status == 404
            else "CATALOG_NOT_VERIFIED"
        )
        item = {
            "bucket": bucket,
            "management_http": status,
            "rest_http": rest_status,
            "state": state,
            "error_codes": [e.get("code") for e in body.get("errors") or []],
            "physical_parquet_is_not_registration": True,
            "catalog_mutations": 0,
            "licensed_migrations": 0,
        }
        results.append(item)
        print(json.dumps(item), flush=True)
    (OUT / "catalog-reconciliation.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
