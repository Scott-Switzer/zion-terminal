"""Read-only operator client for the authenticated local Zion dataset bridge."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["catalog", "health", "list", "head", "read"])
    parser.add_argument(
        "store", nargs="?", choices=["financial", "research", "filings", "lakehouse"]
    )
    parser.add_argument("--url", default="http://127.0.0.1:8791")
    parser.add_argument(
        "--hosted",
        action="store_true",
        help="use authenticated ZION_PRIVATE_BASE_URL and ZION_PRIVATE_API_TOKEN",
    )
    parser.add_argument("--prefix", default="")
    parser.add_argument("--path")
    parser.add_argument("--cursor")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--length", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    headers = {"User-Agent": "zion-private-dataset-client/1"}
    if args.hosted:
        args.url = os.environ.get("ZION_PRIVATE_BASE_URL", "")
        base = urlparse(args.url)
        if (
            base.scheme != "https"
            or not base.hostname
            or base.username
            or base.password
            or base.query
            or base.fragment
            or base.path not in ("", "/")
        ):
            parser.error("ZION_PRIVATE_BASE_URL must be a credential-free HTTPS origin.")
        token = os.environ.get("ZION_PRIVATE_API_TOKEN", "")
        if not token:
            parser.error("ZION_PRIVATE_API_TOKEN is required.")
        headers["Authorization"] = "Bearer " + token
    else:
        base = urlparse(args.url)
        if (
            base.scheme != "http"
            or base.hostname not in {"localhost", "127.0.0.1", "::1"}
            or base.username
            or base.password
            or base.query
            or base.fragment
        ):
            parser.error("Use the loopback-only operator bridge or --hosted.")

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, response_headers, newurl):
            return None

    def open_request(url, method="GET", extra=None, timeout=30):
        req = Request(url, method=method, headers={**headers, **(extra or {})})
        return build_opener(NoRedirect()).open(req, timeout=timeout)

    if args.action in {"list", "head", "read"} and not args.store:
        parser.error("A store is required.")
    if args.action in {"head", "read"} and not args.path:
        parser.error("--path is required.")
    if args.action == "read" and not args.output:
        parser.error("--output is required; private records are never printed automatically.")
    if args.length is not None and not 1 <= args.length <= 1048576:
        parser.error("--length must be 1..1048576.")
    if args.offset < 0 or (args.offset and args.length is None):
        parser.error("Use nonnegative --offset and explicit --length for ranges.")
    if args.action in {"catalog", "health"}:
        route = "/v1/datasets" if args.action == "catalog" else "/healthz"
    elif args.action == "list":
        query = {
            "prefix": args.prefix,
            "limit": args.limit,
            "recursive": str(args.recursive).lower(),
        }
        if args.cursor:
            query["cursor"] = args.cursor
        route = f"/v1/datasets/{args.store}/objects?" + urlencode(query)
    else:
        route = f"/v1/datasets/{args.store}/object?" + urlencode({"path": args.path})
    if args.hosted:
        if args.action == "health":
            parser.error("Use catalog for hosted store discovery.")
        route = "/private" + route
    url = args.url.rstrip("/") + route
    if args.action in {"read", "head"}:
        with open_request(url, method="HEAD", timeout=20) as r:
            etag, size = r.headers["ETag"], int(r.headers["Content-Length"])
        if args.action == "head":
            print(json.dumps({"bytes": size, "etag": etag}))
            return
        if args.length is not None:
            url += "&" + urlencode({"offset": args.offset, "length": args.length})
        with open_request(url, extra={"If-Match": etag}) as r:
            data = r.read(1048577)
        if len(data) > 1048576:
            raise ValueError("Response exceeded read budget.")
        with args.output.open("xb") as target:
            target.write(data)
        print(json.dumps({"bytes_written": len(data), "etag": etag, "output": str(args.output)}))
    else:
        with open_request(url, timeout=20) as r:
            data = r.read(1048577)
        if len(data) > 1048576:
            raise ValueError("Response exceeded metadata budget.")
        print(json.dumps(json.loads(data), indent=2))


if __name__ == "__main__":
    main()
