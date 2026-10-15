"""Private, source-pinned equity claims endpoint.

Reads the immutable SEC engine archive member for one filer and returns the versioned
equity claims contract. Nothing is fetched at request time and no licensed source is
touched. Authentication is either the private bearer credential or a scoped internal
service credential that only this route accepts.
"""

import asyncio
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

from equity_claims import VERSION, build_claims
from private_financials import PrivateFinancialsError
from private_http import authorize
from sec_engine_api import artifact, entry, root
from temporal_core import parse_instant

PATH = "/private/v1/equity-claims"
PARAMETERS = {"cik", "as_of", "expected_release_id", "recipient"}
MAX_RESPONSE = 2 * 1048576


def fail(code, status=400):
    raise PrivateFinancialsError(code, code, status)


def parameters(url):
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    if set(query) - PARAMETERS or any(len(values) != 1 for values in query.values()):
        fail("INVALID_PARAMETER")
    return {key: values[0] for key, values in query.items()}


async def execute(env, request):
    audit = authorize(env, request, service_paths=(PATH,))
    if request.method != "GET":
        fail("READ_ONLY", 405)
    p = parameters(request.url)
    cik = p.get("cik", "")
    if not re.fullmatch(r"\d{10}", cik):
        fail("INVALID_CIK")
    if p.get("recipient", "human_research") != "human_research":
        fail("RECIPIENT_NOT_PERMITTED", 403)
    if not p.get("as_of"):
        fail("CUTOFF_REQUIRED", 422)
    try:
        cutoff = parse_instant(p["as_of"])
    except (ValueError, TypeError):
        fail("INVALID_AVAILABILITY_CUTOFF", 400)
    if cutoff.epoch_ns > parse_instant(datetime.now(timezone.utc).isoformat()).epoch_ns:
        fail("CUTOFF_EXCEEDS_SOURCE_RELEASE", 422)
    valuation_date = date.fromisoformat(cutoff.iso_utc[:10])
    manifest = await root(env)
    if p.get("expected_release_id", manifest["release_id"]) != manifest["release_id"]:
        fail("SOURCE_VERSION_CHANGED", 409)
    item = await entry(env, manifest, cik)
    if item.get("maximum_as_of") and cutoff.epoch_ns > parse_instant(item["maximum_as_of"]).epoch_ns:
        fail("CUTOFF_EXCEEDS_SOURCE_RELEASE", 422)
    reference = item.get("materialized")
    if not isinstance(reference, dict):
        fail("EQUITY_CLAIMS_SOURCE_NOT_MATERIALIZED", 422)
    company = await artifact(env, reference)
    if str(company.get("cik")).zfill(10) != cik or not isinstance(company.get("facts"), dict):
        fail("SOURCE_CONTEXT_INVALID", 503)
    try:
        claims = build_claims(company["facts"], valuation_date, cik=cik)
    except ValueError as error:
        fail(str(error), 422)
    result = dict(
        claims,
        release_id=manifest["release_id"],
        registry_sha256=manifest["registry_sha256"],
        manifest_sha256=manifest.get(
            "_manifest_sha256", getattr(env, "SEC_ENGINE_MANIFEST_SHA256", None)
        ),
        member_sha256=reference.get("sha256"),
        company_label=company.get("entityName"),
        audit=audit,
        provenance={
            "engine": "NATIVE_IMMUTABLE_SEC_EQUITY_CLAIMS",
            "source": "SEC_COMPANY_FACTS_PINNED_ARCHIVE_MEMBER",
            "extraction_version": VERSION,
            "release_id": manifest["release_id"],
            "registry_sha256": manifest["registry_sha256"],
            "member": reference.get("key"),
            "source_lineage": {
                "rights": "PUBLIC_SEC_ONLY",
                "licensed_sources_used": False,
            },
            "archive_bytes_read_interactively": 0,
        },
    )
    if len(json.dumps(result, separators=(",", ":"), allow_nan=False).encode()) > MAX_RESPONSE:
        fail("RESPONSE_BUDGET_EXCEEDED", 413)
    return result, 200


async def handle(env, request):
    try:
        return await asyncio.wait_for(execute(env, request), timeout=25)
    except PrivateFinancialsError as error:
        return {"error": {"code": error.code}}, error.status
    except TimeoutError:
        return {"error": {"code": "QUERY_TIMEOUT"}}, 504
    except Exception:
        return {"error": {"code": "EQUITY_CLAIMS_UNAVAILABLE"}}, 503
