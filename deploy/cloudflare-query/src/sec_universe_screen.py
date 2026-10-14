"""Stream one hash-pinned metric's SEC chunks; retain only a page and missingness."""

import asyncio
import re
from fractions import Fraction

import sec_screener as legacy
from private_financials import PrivateFinancialsError
from private_http import authenticate
from sec_engine import encoded
from sec_engine_api import artifact, cursor_decode, cursor_encode, fail, parameters, root, sha

VERSION = "sec-screen-metric-shards-v1"
MAX_CHUNK_BYTES = 2 * 1048576
MAX_INDEX_BYTES = 32 * 1048576
MAX_CHUNKS = 32
MAX_RESPONSE = 2 * 1048576
MAX_COMPANIES = 25000
PARAMETERS = {
    "metric",
    "minimum",
    "maximum",
    "sort",
    "ciks",
    "limit",
    "cursor",
    "as_of",
    "expected_release_id",
    "recipient",
}


def threshold(value):
    if value is None:
        return None
    match = re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d{1,3}))?", value)
    if len(value) > 128 or not match or (match.group(1) and abs(int(match.group(1))) > 80):
        fail("INVALID_SCREEN_THRESHOLD", 422)
    return legacy.exact(value)


def numeric(value, metric):
    if metric == "revenue_growth":
        numerator, denominator = value["numerator"], value["denominator"]
        if (
            not all(
                isinstance(v, str) and re.fullmatch(r"-?\d{1,256}", v)
                for v in (numerator, denominator)
            )
            or int(denominator) <= 0
        ):
            fail("SCREEN_SOURCE_VALUE_INVALID", 409)
        return Fraction(int(numerator), int(denominator))
    return threshold(value["value_decimal"])


async def execute(env, request):
    authenticate(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    q = parameters(request.url, PARAMETERS)
    if q.get("recipient", "human_research") != "human_research":
        fail("RECIPIENT_NOT_PERMITTED", 403)
    metric, direction = q.get("metric", "revenue_growth"), q.get("sort", "desc")
    limit = int(q.get("limit", "25"))
    if metric not in legacy.METRICS or direction not in ("asc", "desc") or not 1 <= limit <= 100:
        fail("INVALID_SCREEN_PARAMETER", 422)
    lower, upper = threshold(q.get("minimum")), threshold(q.get("maximum"))
    if lower is not None and upper is not None and lower > upper:
        fail("INVALID_THRESHOLD_RANGE", 422)
    ciks = q["ciks"].split(",") if q.get("ciks") else None
    if ciks and (
        len(ciks) > MAX_COMPANIES
        or len(ciks) != len(set(ciks))
        or any(not re.fullmatch(r"\d{10}", cik) for cik in ciks)
    ):
        fail("INVALID_SCREEN_CIKS", 422)
    requested = set(ciks) if ciks else None
    manifest = await root(env)
    if q.get("expected_release_id", manifest["release_id"]) != manifest["release_id"]:
        fail("SOURCE_VERSION_CHANGED", 409)
    if q.get("as_of", manifest["maximum_as_of"]) != manifest["maximum_as_of"]:
        fail("SCREEN_SUPPORTS_FIXED_SNAPSHOT_CUTOFF_ONLY", 422)
    index = manifest.get("screening_metrics")
    if index is None:
        return await legacy.handle(env, request)
    if index.get("schema_version") != VERSION:
        fail("SCREEN_SOURCE_POLICY_OR_VERSION_INVALID", 403)
    refs = index.get("metrics", {}).get(metric)
    if not isinstance(refs, list) or not refs:
        fail("SCREEN_INDEX_NOT_PUBLISHED", 503)
    if len(refs) > MAX_CHUNKS:
        fail("SCREEN_INDEX_BUDGET_EXCEEDED", 413)
    declared_bytes = 0
    for ref in refs:
        size = ref.get("bytes", MAX_CHUNK_BYTES)
        if type(size) is not int or not 0 < size <= MAX_CHUNK_BYTES:
            fail("SCREEN_SOURCE_SIZE_INVALID", 409)
        declared_bytes += size
    if declared_bytes > MAX_INDEX_BYTES:
        fail("SCREEN_INDEX_BUDGET_EXCEEDED", 413)
    fingerprint = sha(encoded({k: v for k, v in q.items() if k not in ("cursor", "limit")}))
    offset = 0
    if q.get("cursor"):
        cursor = cursor_decode(q["cursor"], env.ZION_PRIVATE_API_TOKEN)
        if cursor.get("query") != fingerprint or cursor.get("release") != manifest["release_id"]:
            fail("CURSOR_QUERY_OR_RELEASE_MISMATCH", 409)
        offset = cursor.get("offset")
        if type(offset) is not int or not 0 <= offset <= MAX_COMPANIES:
            fail("INVALID_CURSOR_OFFSET", 422)
    reverse = direction == "desc"
    seen, matches, missing = set(), [], []
    index_bytes = missing_bytes = found = selected_companies = 0
    previous = None
    for ref in reversed(refs) if reverse else refs:
        chunk = await artifact(env, ref, MAX_CHUNK_BYTES)
        canonical_bytes = len(encoded(chunk))
        if canonical_bytes > ref.get("bytes", MAX_CHUNK_BYTES):
            fail("SCREEN_SOURCE_SIZE_INVALID", 409)
        index_bytes += ref.get("bytes", MAX_CHUNK_BYTES)
        if index_bytes > MAX_INDEX_BYTES:
            fail("SCREEN_INDEX_BUDGET_EXCEEDED", 413)
        if (
            chunk.get("release_id") != manifest["release_id"]
            or chunk.get("registry_sha256") != manifest["registry_sha256"]
        ):
            fail("SOURCE_VERSION_CHANGED", 409)
        companies = chunk.get("companies")
        if not isinstance(companies, list):
            fail("SCREEN_SOURCE_POLICY_OR_VERSION_INVALID", 403)
        for company in reversed(companies) if reverse else companies:
            cik = company["cik"]
            if not isinstance(cik, str) or not re.fullmatch(r"\d{10}", cik) or cik in seen:
                fail("SCREEN_SOURCE_DUPLICATE_OR_INVALID_CIK", 409)
            seen.add(cik)
            if len(seen) > MAX_COMPANIES:
                fail("SCREEN_UNIVERSE_BUDGET_EXCEEDED", 413)
            value = company["metrics"].get(metric)
            number = numeric(value, metric) if value is not None else None
            key = (number is None, number if number is not None else Fraction(0), cik)
            if previous is not None and (
                (reverse and key > previous) or (not reverse and key < previous)
            ):
                fail("SCREEN_SOURCE_ORDER_INVALID", 409)
            previous = key
            if requested is not None and cik not in requested:
                continue
            selected_companies += 1
            if value is None:
                item = {"cik": cik, "reason": company.get("missing", {}).get(metric)}
                missing_bytes += len(encoded(item)) + 1
                if missing_bytes > MAX_RESPONSE:
                    fail("SCREEN_RESPONSE_BUDGET_EXCEEDED", 413)
                missing.append(item)
                continue
            if (lower is not None and number < lower) or (upper is not None and number > upper):
                continue
            if offset <= found < offset + limit:
                matches.append(
                    {
                        "cik": cik,
                        "label": company.get("label"),
                        "statement_family": company.get("statement_family"),
                        "member_sha256": company.get("member_sha256"),
                        "metric": metric,
                        "observation": value,
                    }
                )
            found += 1
    if requested is not None and requested - seen:
        fail("SCREEN_CIK_NOT_MATERIALIZED", 404)
    if offset > found:
        fail("INVALID_CURSOR_OFFSET", 422)
    end = min(found, offset + limit)
    result = {
        "release_id": manifest["release_id"],
        "as_of": manifest["maximum_as_of"],
        "frequency": "annual",
        "metric": metric,
        "matches": matches,
        "total_matches": found,
        "universe_companies": selected_companies,
        "missing_companies": missing,
        "missingness_policy": "EXCLUDED_WITH_REASON;NEVER_ZERO_IMPUTED",
        "next_cursor": cursor_encode(
            {"query": fingerprint, "release": manifest["release_id"], "offset": end},
            env.ZION_PRIVATE_API_TOKEN,
        )
        if end < found
        else None,
        "provenance": {
            "screening_metric_sha256": [ref["sha256"] for ref in refs],
            "registry_sha256": manifest["registry_sha256"],
            "licensed_sources_used": False,
        },
        "qualification": index.get("qualification", "LATEST_VALID_ANNUAL;FISCAL_ENDS_MAY_DIFFER"),
        "ordering": "ASC_VALUE_ASC_CIK;DESC_VALUE_DESC_CIK;MISSING_EXCLUDED",
        "limits": {
            "max_page_matches": 100,
            "index_bytes": index_bytes,
            "max_index_bytes": MAX_INDEX_BYTES,
            "max_chunks": MAX_CHUNKS,
            "response_bytes": MAX_RESPONSE,
            "archive_bytes_read": 0,
        },
    }
    if len(encoded(result)) > MAX_RESPONSE:
        fail("SCREEN_RESPONSE_BUDGET_EXCEEDED", 413)
    return result, 200


async def handle(env, request):
    try:
        return await asyncio.wait_for(execute(env, request), timeout=20)
    except PrivateFinancialsError as error:
        return {"error": {"code": error.code}}, error.status
    except (ValueError, TypeError):
        return {"error": {"code": "INVALID_SCREEN_PARAMETER_OR_CURSOR"}}, 422
    except TimeoutError:
        return {"error": {"code": "SCREEN_QUERY_TIMEOUT"}}, 504
    except Exception:
        return {"error": {"code": "SCREEN_UNAVAILABLE"}}, 503
