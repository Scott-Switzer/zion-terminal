"""Verify authenticated full-store discovery and bounded source reads, without logging data."""

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlparse

from private_serving_acceptance import NoRedirect


def run(base, token):
    parsed = urlparse(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not token
    ):
        raise ValueError("credential-free HTTPS origin and runtime token required")
    checks = []

    def execute(name, path, *, method="GET", etag=None, authorized=True, binary=False):
        headers = {"user-agent": "zion-private-collection-acceptance/1"}
        if authorized:
            headers["authorization"] = "Bearer " + token
        if etag:
            headers["if-match"] = etag
        req = urllib.request.Request(base.rstrip("/") + path, method=method, headers=headers)
        try:
            response = urllib.request.build_opener(NoRedirect()).open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        except Exception:
            checks.append({"name": name, "http_status": 0, "status": "FAIL"})
            return {}, {}, b""
        with response:
            raw = response.read(1048577)
            check = {"name": name, "http_status": response.code, "status": "FAIL"}
            checks.append(check)
            check["private_no_store"] = response.headers.get("cache-control") == "private, no-store"
            if len(raw) > 1048576:
                return check, {}, b""
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            check["status"] = (
                "PASS" if response.code in (200, 206) and check["private_no_store"] else "FAIL"
            )
            return check, dict(response.headers), raw if binary else body

    c, _, _ = execute("unauthenticated", "/private/v1/datasets", authorized=False)
    c["status"] = "PASS" if c.get("http_status") == 401 else "FAIL"
    c, _, catalog = execute("catalog", "/private/v1/datasets")
    sources = catalog.get("sources", []) if isinstance(catalog, dict) else []
    c["status"] = (
        "PASS"
        if c.get("http_status") == 200
        and {s["id"] for s in sources} == {"financial", "research", "filings", "lakehouse"}
        else "FAIL"
    )
    for store in ("financial", "research", "filings", "lakehouse"):
        path = f"/private/v1/datasets/{store}/objects?" + urlencode(
            {"limit": 2, "recursive": "true"}
        )
        c, _, listing = execute(store + ":list", path)
        objects = listing.get("objects", []) if isinstance(listing, dict) else []
        objects = [obj for obj in objects if obj.get("bytes", 0) > 0]
        seen = set()
        for page in range(10):
            if objects or not isinstance(listing, dict) or not listing.get("truncated"):
                break
            cursor = listing.get("cursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
            path = f"/private/v1/datasets/{store}/objects?" + urlencode(
                {"limit": 2, "recursive": "true", "cursor": cursor}
            )
            c, _, listing = execute(f"{store}:continuation:{page}", path)
            objects = listing.get("objects", []) if isinstance(listing, dict) else []
            objects = [obj for obj in objects if obj.get("bytes", 0) > 0]
        if not objects:
            c["status"] = "FAIL"
            c["reason"] = "NO_NONEMPTY_SAMPLE_WITHIN_PAGE_BUDGET"
            continue
        item = objects[0]
        path = f"/private/v1/datasets/{store}/object?" + urlencode({"path": item["path"]})
        c, h, _ = execute(store + ":head", path, method="HEAD")
        etag = next((v for k, v in h.items() if k.lower() == "etag"), None)
        if not etag:
            c["status"] = "FAIL"
            continue
        c, _, raw = execute(
            store + ":range", path + "&offset=0&length=4096", etag=etag, binary=True
        )
        c["status"] = (
            "PASS"
            if c.get("http_status") == 206
            and c.get("private_no_store")
            and isinstance(raw, bytes)
            and 0 < len(raw) <= 4096
            else "FAIL"
        )
        c["bytes_read"] = len(raw)
        if isinstance(raw, bytes):
            c["range_sha256"] = hashlib.sha256(raw).hexdigest()
        c, _, _ = execute(
            store + ":changed_etag", path + "&offset=0&length=1", etag='"invalid-version"'
        )
        c["status"] = "PASS" if c.get("http_status") == 412 else "FAIL"
    return {
        "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
        "stores": len(sources),
        "checks": checks,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run(os.environ["ZION_PRIVATE_BASE_URL"], os.environ["ZION_PRIVATE_API_TOKEN"])
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if report["status"] == "PASS" else 1)
