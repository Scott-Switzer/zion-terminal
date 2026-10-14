"""Authenticated public-only immutable SEC history endpoint. No SQL/provider access."""

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from private_financials import PrivateFinancialsError
from private_http import authenticate
from sec_history import REGISTRY, VERSION, HistoryError, history
from temporal_core import parse_instant

PREFIX = "gold/sec-financial-history/releases/"
MAX_SOURCE = 16 * 1048576
MAX_RESPONSE = 2 * 1048576


def fail(code, status=400):
    raise PrivateFinancialsError(code, code, status)


def validate_query(url):
    q = parse_qs(urlparse(url).query, keep_blank_values=True)
    if set(q) - {
        "cik",
        "metrics",
        "frequency",
        "date_from",
        "date_to",
        "as_of",
        "expected_release_id",
        "recipient",
    } or any(len(v) != 1 for v in q.values()):
        fail("INVALID_PARAMETER")
    p = {k: v[0] for k, v in q.items()}
    if not re.fullmatch(r"\d{10}", p.get("cik", "")):
        fail("INVALID_CIK")
    if p.get("recipient", "human_research") != "human_research":
        fail("RECIPIENT_NOT_PERMITTED", 403)
    p["metrics"] = p.get(
        "metrics",
        "revenue,operating_income,net_income,operating_cash_flow,capital_expenditures,cash,total_debt,diluted_shares",
    ).split(",")
    p["frequency"] = p.get("frequency", "annual")
    if any(m not in REGISTRY for m in p["metrics"]) or len(set(p["metrics"])) != len(p["metrics"]):
        fail("INVALID_METRIC", 422)
    if p["frequency"] not in ("annual", "quarterly", "ttm"):
        fail("INVALID_FREQUENCY", 422)
    return p


async def artifact(env, ref, maximum):
    key = ref.get("key", "")
    sha = ref.get("sha256", "")
    if (
        not key.startswith(PREFIX)
        or any(x in ("", ".", "..") for x in key.split("/"))
        or not re.fullmatch("[a-f0-9]{64}", sha)
    ):
        fail("SOURCE_REFERENCE_INVALID", 503)
    obj = await env.MARKET_DATA.get(key)
    if not obj or obj.size > maximum:
        fail("SOURCE_SIZE_BUDGET_OR_MISSING", 503)
    raw = (await obj.text()).encode()
    if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != sha:
        fail("SOURCE_VERSION_CHANGED", 409)
    return json.loads(raw)


async def execute(env, request):
    authenticate(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    p = validate_query(request.url)
    manifest = await artifact(
        env,
        {
            "key": getattr(env, "SEC_HISTORY_MANIFEST_KEY", ""),
            "sha256": getattr(env, "SEC_HISTORY_MANIFEST_SHA256", ""),
        },
        262144,
    )
    if (
        manifest.get("schema_version") != VERSION
        or manifest.get("rights") != "PUBLIC_SEC_ONLY"
        or manifest.get("licensed_sources_allowed") is not False
    ):
        fail("SOURCE_POLICY_NOT_PERMITTED", 403)
    if p.get("expected_release_id", manifest["release_id"]) != manifest["release_id"]:
        fail("SOURCE_VERSION_CHANGED", 409)
    ref = manifest["companies"].get(p["cik"])
    if not ref:
        fail("CIK_NOT_APPROVED", 403)
    data = await artifact(env, ref, MAX_SOURCE)
    if (
        data.get("release_id") != manifest["release_id"]
        or data.get("registry_sha256") != manifest["registry_sha256"]
        or data.get("cik") != p["cik"]
        or len(data.get("observations", [])) > 5000
    ):
        fail("SOURCE_CONTEXT_INVALID", 503)
    p.setdefault("date_from", manifest["date_from"])
    p.setdefault("date_to", manifest["date_to"])
    if p["date_from"] < manifest["date_from"] or p["date_to"] > manifest["date_to"]:
        fail("DATE_RANGE_OUTSIDE_VERIFIED_RELEASE", 422)
    as_of = p.get("as_of", manifest["maximum_as_of"])
    try:
        instant = parse_instant(as_of)
        if (
            instant.epoch_ns > parse_instant(manifest["maximum_as_of"]).epoch_ns
            or instant.epoch_ns > parse_instant(datetime.now(timezone.utc).isoformat()).epoch_ns
        ):
            fail("CUTOFF_EXCEEDS_SOURCE_RELEASE", 422)
        result = history(
            data,
            metrics=p["metrics"],
            frequency=p["frequency"],
            date_from=p["date_from"],
            date_to=p["date_to"],
            as_of=as_of,
        )
    except HistoryError as e:
        fail(e.code, 422)
    except (ValueError, TypeError):
        fail("INVALID_AVAILABILITY_CUTOFF", 400)
    if len(result["observations"]) > 1000:
        fail("ROW_BUDGET_EXCEEDED", 413)
    # Complete leaf source records for the result's dependency graph, without invented records.
    ids = {sid for row in result["observations"] for sid in row["source_observation_ids"]}
    result["source_records"] = {
        r["source_observation_id"]: {
            **r,
            "source_record": {**r["source_record"], "val": r["value_decimal"]},
            "source_value_encoding": "EXACT_DECIMAL_STRING;ORIGINAL_TOKEN_IN_PINNED_ARCHIVE",
        }
        for r in data["observations"]
        if r["source_observation_id"] in ids
    }
    if set(result["source_records"]) != ids:
        fail("SOURCE_LINEAGE_INCOMPLETE", 503)
    result["provenance"] = {
        "engine": "NATIVE_IMMUTABLE_SEC_HISTORY",
        "release_id": manifest["release_id"],
        "manifest_sha256": getattr(env, "SEC_HISTORY_MANIFEST_SHA256"),
        "company_payload_sha256": ref["sha256"],
        "registry_sha256": manifest["registry_sha256"],
        "source_lineage": data["source_lineage"],
        "concept_definitions": {
            r["concept"]: data.get("concept_definitions", {}).get(r["concept"])
            for r in result["source_records"].values()
        },
        "licensed_sources_used": False,
        "canonical_financial_certification": False,
    }
    result["source_quality_rejections"] = data.get("rejections", {})
    result["limits"] = {
        "source_bytes": MAX_SOURCE,
        "response_bytes": MAX_RESPONSE,
        "max_observations": 1000,
        "max_source_records": 5000,
        "caller_timeout_seconds": 20,
    }
    if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_RESPONSE:
        fail("RESPONSE_BUDGET_EXCEEDED", 413)
    return result


async def handle(env, request):
    try:
        return await asyncio.wait_for(execute(env, request), timeout=20), 200
    except PrivateFinancialsError as e:
        return {"error": {"code": e.code}}, e.status
    except TimeoutError:
        return {"error": {"code": "QUERY_TIMEOUT"}}, 504
    except Exception:
        return {"error": {"code": "SEC_HISTORY_UNAVAILABLE"}}, 503
