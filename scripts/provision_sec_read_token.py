"""Try minimal bucket-scoped read token. Never prints or logs secret material."""

import json
import os
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from publish_basin_public_pilot import ACCOUNT, BUCKET, token

OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-financial-history")


def main():
    policy = {
        "effect": "allow",
        "resources": {f"com.cloudflare.edge.r2.bucket.{ACCOUNT}_default_{BUCKET}": "*"},
        "permission_groups": [
            {"id": "f45430d92e2b4a6cb9f94f2594c141b8"},
            {"id": "45db74139a62490b9b60eb7c4f34994b"},
            {"id": "6a018a9f2fc74eb6b293b0c548f38b39"},
        ],
    }
    probes = []
    credentials = [
        ("operator", token(), f"/accounts/{ACCOUNT}/tokens"),
        (
            "wrangler_oauth",
            tomllib.loads((Path.home() / ".wrangler/config/default.toml").read_text())[
                "oauth_token"
            ],
            "/user/tokens",
        ),
    ]
    for name, cred, endpoint in credentials:
        req = urllib.request.Request(
            "https://api.cloudflare.com/client/v4" + endpoint,
            data=json.dumps(
                {"name": "zion-sec-history-readonly-v1", "policies": [policy]}
            ).encode(),
            headers={
                "Authorization": "Bearer " + cred,
                "Content-Type": "application/json",
                "User-Agent": "zion-sec-least-privilege/1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, data = r.status, json.load(r)
        except urllib.error.HTTPError as e:
            status, data = e.code, json.load(e)
        probes.append(
            {
                "credential": name,
                "http_status": status,
                "error_codes": [e.get("code") for e in data.get("errors") or []],
            }
        )
        if data.get("success") and data.get("result", {}).get("value"):
            p = Path.home() / ".config/zion/sec-history-basin-readonly.env"
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(
                    "BASIN_SQL_TOKEN="
                    + data["result"]["value"]
                    + "\nBASIN_TOKEN_ID="
                    + data["result"]["id"]
                    + "\n"
                )
            probes[-1].update(
                token_created=True,
                secret_destination=str(p),
                permission_groups=["SQL_READ", "CATALOG_READ", "STORAGE_BUCKET_ITEM_READ"],
            )
            break
    (OUT / "least-privilege-provisioning.json").write_text(
        json.dumps(
            {"attempts": probes, "runtime_validated": False, "scope": policy["resources"]}, indent=2
        )
        + "\n"
    )
    print(json.dumps(probes))


if __name__ == "__main__":
    main()
