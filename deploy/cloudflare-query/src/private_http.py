"""Private API authentication and bounded HTTP parsing, without secret logging."""

import hashlib
import hmac
import json
from urllib.parse import urlparse

from private_api import OPERATIONS, query_existing
from private_financials import PrivateFinancialsError

MAXIMUM_BODY = 32768


def error(code, message, status):
    raise PrivateFinancialsError(code, message, status)


def authenticate(env, request):
    expected = getattr(env, "ZION_PRIVATE_API_TOKEN", None)
    if not isinstance(expected, str) or not expected:
        error("PRIVATE_API_UNCONFIGURED", "private API is not configured", 503)
    supplied = request.headers.get("authorization", "")
    if not isinstance(supplied, str) or len(supplied) > 4096:
        error("UNAUTHORIZED", "valid bearer authentication is required", 401)
    prefix, separator, token = supplied.partition(" ")
    if (
        prefix.lower() != "bearer"
        or not separator
        or not token
        or not hmac.compare_digest(
            hashlib.sha256(token.encode()).digest(), hashlib.sha256(expected.encode()).digest()
        )
    ):
        error("UNAUTHORIZED", "valid bearer authentication is required", 401)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


async def read_body(request):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if content_type != "application/json":
        error("INVALID_REQUEST", "application/json is required", 415)
    declared = request.headers.get("content-length")
    if declared is not None:
        if not str(declared).isdigit():
            error("INVALID_REQUEST", "invalid content length", 400)
        if int(declared) > MAXIMUM_BODY:
            error("REQUEST_TOO_LARGE", "request exceeds 32 KiB", 413)
    if request.body is None:
        error("INVALID_REQUEST", "a JSON object is required", 400)
    reader = request.body.getReader()
    raw = bytearray()
    try:
        while True:
            result = await reader.read()
            if result.done:
                break
            if len(raw) + result.value.byteLength > MAXIMUM_BODY:
                await reader.cancel()
                error("REQUEST_TOO_LARGE", "request exceeds 32 KiB", 413)
            raw.extend(bytes(result.value.to_py()))
    finally:
        reader.releaseLock()
    try:
        body = json.loads(
            raw,
            object_pairs_hook=unique_object,
            parse_constant=lambda _: error("INVALID_REQUEST", "finite JSON required", 400),
        )
    except (ValueError, UnicodeError, RecursionError):
        error("INVALID_REQUEST", "valid bounded JSON is required", 400)
    if not isinstance(body, dict):
        error("INVALID_REQUEST", "a JSON object is required", 400)
    return body


async def handle_private(env, request, *, request_id):
    try:
        authenticate(env, request)
        path = urlparse(request.url).path
        if request.method == "GET" and path == "/private/v1/capabilities":
            return {
                "schema_version": "zion-private-capabilities-v1",
                "operations": list(OPERATIONS),
                "acquisition_enabled": False,
                "as_of_format": "timezone-aware RFC3339 instant",
                "pending": ["estimates", "macro", "historical_research"],
                "scope": "full private financial storage plus explicit normalized query operations",
                "datasets": {
                    "base_path": "/private/v1/datasets",
                    "stores": ["financial", "research", "filings", "lakehouse"],
                    "access": "READ_ONLY_SOURCE_BYTES",
                    "discovery": "PAGINATED_ALL_OBJECTS",
                    "normalization": "SOURCE_SPECIFIC_NOT_UNIVERSAL",
                },
            }, 200
        if request.method != "POST" or path != "/private/v1/query":
            error("NOT_FOUND", "private route not found", 404)
        body = await read_body(request)
        return await query_existing(env, body, request_id=request_id), 200
    except PrivateFinancialsError as exc:
        return {
            "error": {
                "request_id": request_id,
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.status == 503,
            }
        }, exc.status
    except Exception:
        return {
            "error": {
                "request_id": request_id,
                "code": "DATA_UNAVAILABLE",
                "message": "private data is unavailable",
                "retryable": True,
            }
        }, 503
