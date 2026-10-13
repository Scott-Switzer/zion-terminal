"""Authenticated streaming access to the existing private dataset service."""

import json
from urllib.parse import urlparse

from private_financials import PrivateFinancialsError
from private_http import authenticate

PREFIX = "/private/v1/datasets"
RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "etag",
    "accept-ranges",
    "x-data-quality",
    "x-content-type-options",
)


async def handle(env, request):
    from workers import Request, Response

    headers = {
        "cache-control": "private, no-store",
        "vary": "Authorization",
        "x-content-type-options": "nosniff",
    }
    try:
        authenticate(env, request)
        url = urlparse(request.url)
        if url.path != PREFIX and not url.path.startswith(PREFIX + "/"):
            raise PrivateFinancialsError("NOT_FOUND", "private route not found", 404)
        if request.method not in ("GET", "HEAD"):
            raise PrivateFinancialsError("READ_ONLY", "dataset access is read-only", 405)
        binding = getattr(env, "PRIVATE_DATASETS", None)
        if not binding:
            raise PrivateFinancialsError(
                "DATASET_SERVICE_UNAVAILABLE", "dataset service unavailable", 503
            )
        # Reconstruct the trusted origin; no incoming bearer, cookies or arbitrary host forwarding.
        target = "https://zion-datasets.internal" + url.path.removeprefix("/private")
        if url.query:
            target += "?" + url.query
        forwarded = {}
        if url.path == PREFIX + "/financial-query":
            forwarded["x-zion-principal"] = "private-owner"
        etag = request.headers.get("if-match")
        if etag:
            forwarded["if-match"] = etag
        upstream = Request(target, method=request.method, headers=forwarded)
        response = await binding.fetch(upstream.js_object)
        for key in RESPONSE_HEADERS:
            value = response.headers.get(key)
            if value:
                headers[key] = value
        # Preserve streams and exact bytes, including Parquet/ZIP ranges; never decode the body.
        return Response(response.body, status=response.status, headers=headers)
    except PrivateFinancialsError as exc:
        payload = {"error": {"code": exc.code, "message": exc.message}}
        status = exc.status
    except Exception:
        payload = {
            "error": {
                "code": "DATASET_SERVICE_UNAVAILABLE",
                "message": "dataset service unavailable",
            }
        }
        status = 503
    headers["content-type"] = "application/json"
    return Response(json.dumps(payload), status=status, headers=headers)
