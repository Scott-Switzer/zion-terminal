"""Private API authentication and bounded HTTP parsing, without secret logging."""

import hashlib
import hmac
import json
from urllib.parse import urlparse

from private_api import OPERATIONS, query_existing
from private_financials import PrivateFinancialsError

MAXIMUM_BODY = 32768

# Internal callers that may authenticate with the scoped service credential. A
# service credential is accepted only on the routes named in service_paths, so it
# can never widen access to the bearer-only private API.
SERVICE_CALLERS = ("dcf-valuation-engine",)


def error(code, message, status):
    raise PrivateFinancialsError(code, message, status)


def _bearer(env, supplied):
    expected = getattr(env, "ZION_PRIVATE_API_TOKEN", None)
    if not isinstance(expected, str) or not expected:
        error("PRIVATE_API_UNCONFIGURED", "private API is not configured", 503)
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


def authenticate(env, request):
    _bearer(env, request.headers.get("authorization", ""))


def authorize(env, request, *, service_paths=()):
    """Authenticate a request and record how it was authenticated.

    The private bearer credential keeps full private-API scope. The internal service
    credential is scoped to the exact routes passed in service_paths, must name an
    approved same-account caller, and is compared in constant time. Neither path
    disables the entitlement checks performed by the route itself.
    """
    path = urlparse(request.url).path
    try:
        _bearer(env, request.headers.get("authorization", ""))
        return {
            "authenticated_via": "BEARER",
            "caller": "private_bearer_client",
            "scope": "PRIVATE_API",
        }
    except PrivateFinancialsError as bearer_error:
        # The scoped credential is only considered on the routes that declare it.
        if path not in service_paths:
            raise
        bearer_status = bearer_error.status
    expected = getattr(env, "ZION_SERVICE_TOKEN", None)
    if not isinstance(expected, str) or not expected:
        if bearer_status == 503:
            error("SERVICE_AUTH_UNCONFIGURED", "internal service authentication is not configured", 503)
        error("UNAUTHORIZED", "valid bearer or internal service authentication is required", 401)
    supplied = request.headers.get("x-zion-service-token", "")
    if (
        not isinstance(supplied, str)
        or not supplied
        or len(supplied) > 4096
        or not hmac.compare_digest(
            hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(expected.encode()).digest()
        )
    ):
        error("UNAUTHORIZED", "valid internal service authentication is required", 401)
    caller = request.headers.get("x-zion-service-caller", "")
    if caller not in SERVICE_CALLERS:
        error("SERVICE_CALLER_NOT_PERMITTED", "internal service caller is not permitted", 403)
    return {
        "authenticated_via": "SERVICE_CREDENTIAL",
        "caller": caller,
        "scope": "SERVICE_BINDING_ROUTE_SCOPE",
        "authorized_paths": list(service_paths),
    }


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
                    "access": "READ_ONLY_SOURCE_BYTES_AND_NATIVE_PARQUET_ROWS",
                    "catalog_path": "/private/v1/datasets/catalog",
                    "native_table_operations": ["schema", "rows"],
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
