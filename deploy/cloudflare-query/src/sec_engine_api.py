"""Private snapshot-bound SEC discovery, exact histories and explicit pagination."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from private_financials import PrivateFinancialsError
from private_http import authenticate
from sec_engine import REGISTRY, VERSION, HistoryError, balance_checks, encoded, history
from sec_expanded_api import js_options
from temporal_core import parse_instant

PREFIX = "gold/sec-engine/releases/"
MAX_SOURCE = 16 * 1048576
MAX_RESPONSE = 2 * 1048576


def fail(code, status=400):
    raise PrivateFinancialsError(code, code, status)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def signed(value, secret):
    return hmac.new(secret.encode(), encoded(value), hashlib.sha256).hexdigest()


def cursor_encode(value, secret):
    return (
        base64.urlsafe_b64encode(encoded({"value": value, "mac": signed(value, secret)}))
        .decode()
        .rstrip("=")
    )


def cursor_decode(cursor, secret):
    try:
        if len(cursor) > 2048:
            raise ValueError()
        obj = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not hmac.compare_digest(obj["mac"], signed(obj["value"], secret)):
            raise ValueError()
        return obj["value"]
    except Exception:
        raise ValueError("INVALID_CURSOR") from None


def page_result(full, query, release, limit, cursor, secret):
    offset = 0
    if cursor:
        c = cursor_decode(cursor, secret)
        if c.get("query") != query or c.get("release") != release:
            raise ValueError("CURSOR_QUERY_OR_RELEASE_MISMATCH")
        offset = c.get("offset")
    observations = full.get("observations", [])
    gaps = full.get("coverage_gaps", [])
    checks = full.get("accounting_checks", [])
    items = (
        [("observations", x) for x in observations]
        + [("coverage_gaps", x) for x in gaps]
        + [("accounting_checks", x) for x in checks]
    )
    if not isinstance(offset, int) or offset < 0 or offset > len(items):
        raise ValueError("INVALID_CURSOR_OFFSET")
    count = min(limit, len(items) - offset)
    while True:
        result = {
            k: v
            for k, v in full.items()
            if k not in ("observations", "coverage_gaps", "accounting_checks", "source_records")
        }
        result.update(observations=[], coverage_gaps=[], accounting_checks=[])
        for kind, item in items[offset : offset + count]:
            result[kind].append(item)
        ids = {
            sid
            for row in result["observations"] + result["accounting_checks"]
            for sid in row.get("source_observation_ids", [])
        }
        result["source_records"] = {sid: full["source_records"][sid] for sid in sorted(ids)}
        end = offset + count
        result["pagination"] = {
            "offset": offset,
            "returned_items": count,
            "total_items": len(items),
            "total_observations": len(observations),
            "total_coverage_gaps": len(gaps),
            "total_accounting_checks": len(checks),
            "next_cursor": cursor_encode(
                {"query": query, "release": release, "offset": end}, secret
            )
            if end < len(items)
            else None,
            "coverage_complete_in_this_response": offset == 0 and end == len(items),
        }
        if len(encoded(result)) <= MAX_RESPONSE:
            return result
        if count <= 1:
            fail("SINGLE_RECORD_RESPONSE_BUDGET", 413)
        count //= 2


async def artifact(env, ref, maximum=MAX_SOURCE):
    key = ref.get("key", "")
    digest = ref.get("sha256", "")
    if (
        not key.startswith(PREFIX)
        or any(x in ("", ".", "..") for x in key.split("/"))
        or not re.fullmatch("[a-f0-9]{64}", digest)
    ):
        fail("SOURCE_REFERENCE_INVALID", 503)
    obj = await env.MARKET_DATA.get(key)
    if not obj or obj.size > maximum:
        fail("SOURCE_SIZE_BUDGET_OR_MISSING", 503)
    raw = (await obj.text()).encode()
    if len(raw) > maximum or sha(raw) != digest:
        fail("SOURCE_VERSION_CHANGED", 409)
    return json.loads(raw)


async def root(env):
    m = await artifact(
        env,
        {
            "key": getattr(env, "SEC_ENGINE_MANIFEST_KEY", ""),
            "sha256": getattr(env, "SEC_ENGINE_MANIFEST_SHA256", ""),
        },
        1048576,
    )
    if (
        m.get("schema_version") != VERSION
        or m.get("rights") != "PUBLIC_SEC_ONLY"
        or m.get("licensed_sources_allowed") is not False
    ):
        fail("SOURCE_POLICY_NOT_PERMITTED", 403)
    return m


def parameters(url, allowed):
    q = parse_qs(urlparse(url).query, keep_blank_values=True)
    if set(q) - set(allowed) or any(len(v) != 1 for v in q.values()):
        fail("INVALID_PARAMETER")
    return {k: v[0] for k, v in q.items()}


HISTORY_PARAMETERS = {
    "cik",
    "ciks",
    "metrics",
    "frequency",
    "date_from",
    "date_to",
    "as_of",
    "expected_release_id",
    "recipient",
    "limit",
    "cursor",
}


def validate(p, m):
    p = dict(p)
    if not re.fullmatch(r"\d{10}", p.get("cik", "")):
        fail("INVALID_CIK")
    if p.get("recipient", "human_research") != "human_research":
        fail("RECIPIENT_NOT_PERMITTED", 403)
    if p.get("expected_release_id", m["release_id"]) != m["release_id"]:
        fail("SOURCE_VERSION_CHANGED", 409)
    metrics = p.get("metrics", ",".join(k for k in REGISTRY if k != "free_cash_flow"))
    p["metrics"] = metrics.split(",") if isinstance(metrics, str) else metrics
    if (
        not p["metrics"]
        or len(set(p["metrics"])) != len(p["metrics"])
        or any(x not in REGISTRY for x in p["metrics"])
    ):
        fail("INVALID_METRIC", 422)
    p.setdefault("frequency", "annual")
    p.setdefault("date_from", max(m["date_from"], "2015-01-01"))
    p.setdefault("date_to", m["date_to"])
    p.setdefault("as_of", m["maximum_as_of"])
    if p["frequency"] not in ("annual", "quarterly", "ttm"):
        fail("INVALID_FREQUENCY", 422)
    try:
        if parse_instant(p["as_of"]).epoch_ns > min(
            parse_instant(m["maximum_as_of"]).epoch_ns,
            parse_instant(datetime.now(timezone.utc).isoformat()).epoch_ns,
        ):
            fail("CUTOFF_EXCEEDS_SOURCE_RELEASE", 422)
        from datetime import date

        lo, hi = date.fromisoformat(p["date_from"]), date.fromisoformat(p["date_to"])
        if (
            lo > hi
            or lo.isoformat() < m["date_from"]
            or hi.isoformat() > m["date_to"]
            or (hi - lo).days > 100 * 366
        ):
            fail("DATE_RANGE_OUTSIDE_VERIFIED_RELEASE", 422)
        p["limit"] = int(p.get("limit", 1000))
        if not 1 <= p["limit"] <= 1000:
            raise ValueError()
    except (ValueError, TypeError):
        fail("INVALID_DATE_CUTOFF_OR_LIMIT", 422)
    return p


async def entry(env, m, cik):
    ref = m["shards"].get(cik[-2:])
    if not ref:
        fail("CIK_NOT_IN_SOURCE_ARCHIVE", 404)
    shard = await artifact(env, ref, 2 * 1048576)
    if cik not in shard:
        fail("CIK_NOT_IN_SOURCE_ARCHIVE", 404)
    item = shard[cik]
    if item.get("quarantine"):
        fail("CIK_QUARANTINED_" + item["quarantine"]["reason"], 422)
    return item


async def stored(env, key, secret, maximum=MAX_SOURCE):
    obj = await env.MARKET_DATA.get(key)
    if not obj:
        return None
    if obj.size > maximum:
        fail("CACHE_SIZE_BUDGET", 503)
    envelope = json.loads(await obj.text())
    if not hmac.compare_digest(envelope.get("mac", ""), signed(envelope.get("value"), secret)):
        fail("CACHE_INTEGRITY_FAILURE", 409)
    return envelope["value"]


async def persist(env, key, value, secret):
    body = encoded({"value": value, "mac": signed(value, secret)})
    if len(body) > MAX_SOURCE:
        fail("PERSISTED_RESULT_BUDGET", 413)
    # Create-only output. A replay can only observe identical authenticated bytes.
    await env.MARKET_DATA.put(
        key,
        body.decode(),
        js_options(
            {
                "onlyIf": {"etagDoesNotMatch": "*"},
                "httpMetadata": {"contentType": "application/json"},
            }
        ),
    )
    actual = await stored(env, key, secret)
    if actual != value:
        fail("IMMUTABLE_CACHE_CONFLICT", 409)


async def company(env, m, cik, e):
    if e.get("materialized"):
        data = await artifact(env, e["materialized"])
    else:
        data = await stored(
            env, PREFIX + m["release_id"] + "/computed/" + cik + ".json", env.ZION_PRIVATE_API_TOKEN
        )
        if data is None:
            return None
    if (
        data.get("release_id") != m["release_id"]
        or data.get("registry_sha256") != m["registry_sha256"]
        or data.get("cik") != cik
        or data.get("rights") != "PUBLIC_SEC_ONLY"
        or len(data.get("observations", [])) > 12000
    ):
        fail("SOURCE_CONTEXT_INVALID", 503)
    return data


def query_id(p, m, member):
    return sha(
        encoded(
            {
                "release": m["release_id"],
                "member_sha256": member,
                "query": {
                    k: p[k]
                    for k in ("cik", "metrics", "frequency", "date_from", "date_to", "as_of")
                },
            }
        )
    )


async def financial_history(env, m, p, allow_queue=True):
    p = validate(p, m)
    e = await entry(env, m, p["cik"])
    data = await company(env, m, p["cik"], e)
    if data is None:
        if not allow_queue:
            fail("MATERIALIZATION_REQUIRED", 202)
        from sec_engine_jobs import submit

        return await submit(
            env, m, {k: v for k, v in p.items() if k not in ("cursor", "ciks")}, [p["cik"]]
        )
    query = query_id(p, m, data["member_sha256"])
    key = PREFIX + m["release_id"] + "/queries/" + query + ".json"
    cached = await stored(env, key, env.ZION_PRIVATE_API_TOKEN)
    cache_state = "HIT" if cached is not None else "MISS"
    if cached is None:
        cached = history(
            data,
            metrics=p["metrics"],
            frequency=p["frequency"],
            date_from=p["date_from"],
            date_to=p["date_to"],
            as_of=p["as_of"],
        )
        cached["accounting_checks"] = balance_checks(data, p["as_of"], p["date_from"], p["date_to"])
        ids = {
            sid
            for row in cached["observations"] + cached["accounting_checks"]
            for sid in row.get("source_observation_ids", [])
        }
        cached["source_records"] = {
            r["source_observation_id"]: {
                **r,
                "source_record": {**r["source_record"], "val": r["value_decimal"]},
                "source_value_encoding": "EXACT_DECIMAL_STRING;ORIGINAL_TOKEN_IN_PINNED_ARCHIVE",
            }
            for r in data["observations"]
            if r["source_observation_id"] in ids
        }
        if ids != set(cached["source_records"]):
            fail("SOURCE_LINEAGE_INCOMPLETE", 503)
        cached["provenance"] = {
            "engine": "NATIVE_IMMUTABLE_SEC_ENGINE_V2",
            "release_id": m["release_id"],
            "registry_sha256": m["registry_sha256"],
            "manifest_sha256": env.SEC_ENGINE_MANIFEST_SHA256,
            "source_lineage": data["source_lineage"],
            "concept_definitions": data["concept_definitions"],
            "licensed_sources_used": False,
            "canonical_financial_certification": False,
        }
        cached["source_quality_rejections"] = data.get("rejections", {})
        await persist(env, key, cached, env.ZION_PRIVATE_API_TOKEN)
    result = page_result(
        cached, query, m["release_id"], p["limit"], p.get("cursor"), env.ZION_PRIVATE_API_TOKEN
    )
    result["cache"] = {
        "state": cache_state,
        "query_fingerprint": query,
        "source_payload_bytes": e.get("materialized", {}).get("bytes"),
        "source_validation": "PINNED_PAYLOAD_REHASHED_BEFORE_CACHE_USE",
        "archive_bytes_read_interactively": 0,
    }
    result["limits"] = {
        "source_bytes": MAX_SOURCE,
        "response_bytes": MAX_RESPONSE,
        "max_source_records": 12000,
        "max_page_items": 1000,
        "caller_timeout_seconds": 25,
    }
    if len(encoded(result)) > MAX_RESPONSE:
        fail("RESPONSE_BUDGET_EXCEEDED", 413)
    return result, 200


async def execute(env, request):
    authenticate(env, request)
    path = urlparse(request.url).path
    if path == "/private/v1/financial-jobs":
        from sec_engine_jobs import jobs_api

        return await jobs_api(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    m = await root(env)
    if path == "/private/v1/financial-history":
        return await financial_history(
            env, m, parameters(request.url, HISTORY_PARAMETERS - {"ciks"})
        )
    if path == "/private/v1/financial-history/batch":
        q = parameters(request.url, HISTORY_PARAMETERS - {"cik", "cursor"})
        ciks = q.pop("ciks", "").split(",")
        if not 1 <= len(ciks) <= 5 or len(set(ciks)) != len(ciks):
            fail("BATCH_REQUIRES_1_TO_5_UNIQUE_CIKS", 422)
        q.setdefault("limit", "100")
        # Three concurrent bounded source reads/calculations at most.
        semaphore = asyncio.Semaphore(3)

        async def run(cik):
            async with semaphore:
                result, status = await financial_history(env, m, {**q, "cik": cik})
                return {"cik": cik, "http_status": status, "result": result}

        results = await asyncio.gather(*(run(cik) for cik in ciks))
        result = {
            "release_id": m["release_id"],
            "companies": results,
            "licensed_sources_used": False,
        }
        if len(encoded(result)) > MAX_RESPONSE:
            fail("BATCH_RESPONSE_BUDGET_EXCEEDED_USE_JOBS", 413)
        return result, 200
    if path == "/private/v1/financial-companies":
        q = parameters(request.url, {"q", "limit", "cursor", "expected_release_id"})
        if q.get("expected_release_id", m["release_id"]) != m["release_id"]:
            fail("SOURCE_VERSION_CHANGED", 409)
        limit = int(q.get("limit", 25))
        if not 1 <= limit <= 100:
            fail("INVALID_LIMIT", 422)
        needle = q.get("q", "").casefold()
        if len(needle) > 100:
            fail("INVALID_SEARCH", 422)
        rows = await artifact(env, m["directory"], 4 * 1048576)
        found = [
            r for r in rows if needle in r["cik"] or needle in (r.get("label") or "").casefold()
        ]
        offset = 0
        fingerprint = sha(encoded({"q": needle, "route": path}))
        if q.get("cursor"):
            c = cursor_decode(q["cursor"], env.ZION_PRIVATE_API_TOKEN)
            if c.get("release") != m["release_id"] or c.get("query") != fingerprint:
                fail("CURSOR_QUERY_OR_RELEASE_MISMATCH", 409)
            offset = c["offset"]
        end = min(len(found), offset + limit)
        return {
            "release_id": m["release_id"],
            "companies": found[offset:end],
            "total": len(found),
            "next_cursor": cursor_encode(
                {"query": fingerprint, "release": m["release_id"], "offset": end},
                env.ZION_PRIVATE_API_TOKEN,
            )
            if end < len(found)
            else None,
            "qualification": (
                "ARCHIVE_CIK_DISCOVERY;US_REPORTING_ELIGIBILITY_VERIFIED_ON_MATERIALIZATION;"
                "NAMES_ONLY_FROM_VERIFIED_MEMBERS"
            ),
        }, 200
    if path in ("/private/v1/financial-metrics", "/private/v1/financial-coverage"):
        q = parameters(request.url, {"cik", "expected_release_id"})
        if q.get("expected_release_id", m["release_id"]) != m["release_id"]:
            fail("SOURCE_VERSION_CHANGED", 409)
        if path.endswith("metrics") and not q.get("cik"):
            return {
                "release_id": m["release_id"],
                "registry_version": VERSION,
                "metrics": REGISTRY,
            }, 200
        p = validate(q, m)
        e = await entry(env, m, p["cik"])
        data = await company(env, m, p["cik"], e)
        if data is None:
            return {
                "cik": p["cik"],
                "status": "NOT_MATERIALIZED",
                "job_endpoint": "/private/v1/financial-jobs",
                "release_id": m["release_id"],
            }, 202
        return {
            "cik": p["cik"],
            "label": data.get("label"),
            "release_id": m["release_id"],
            "policy": data["policy"],
            "source_inventory": data.get("source_inventory"),
            "coverage": e.get("coverage"),
            "metrics": {
                k: {**v, "quarantined_reason": data["policy"]["quarantined_metrics"].get(k)}
                for k, v in REGISTRY.items()
            },
            "normalized_revisions": len(data["observations"]),
            "source_quality_rejections": data.get("rejections", {}),
            "qualification": "SOURCE_COVERAGE_NOT_FULL_FINANCIAL_CERTIFICATION",
        }, 200
    fail("NOT_FOUND", 404)


async def handle(env, request):
    try:
        return await asyncio.wait_for(execute(env, request), timeout=25)
    except PrivateFinancialsError as e:
        return {"error": {"code": e.code}}, e.status
    except HistoryError as e:
        return {"error": {"code": e.code}}, 422
    except ValueError:
        return {"error": {"code": "INVALID_CURSOR_OR_PARAMETER"}}, 400
    except TimeoutError:
        return {"error": {"code": "QUERY_TIMEOUT_USE_JOBS"}}, 504
    except Exception:
        return {"error": {"code": "SEC_ENGINE_UNAVAILABLE"}}, 503
