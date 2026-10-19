"""Private, source-pinned equity claims endpoint.

Claims are derived from Company Facts bytes that two independently published immutable
releases agree on:

* the SEC engine release decides whether the filer is eligible at all, at what cutoff, and
  pins ``materialized.member_sha256`` for the raw archive member it was normalized from;
* the companion Company Facts release carries those exact bytes in the shape the claims
  contract consumes.

Neither archive is mutated, no external provider is fetched at request time, and no caller-supplied
object key is accepted. Authentication is either the private bearer credential or a scoped
internal service credential that only this route accepts.
"""

import asyncio
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

from equity_claims import (
    AVAILABILITY_POLICY,
    AVAILABILITY_RULE,
    VERSION,
    build_claims,
    end_of_day_utc,
    restrict_to_available,
)
from private_financials import PrivateFinancialsError
from private_http import authorize
from sec_companyfacts import (
    SCHEMA_VERSION as SOURCE_SCHEMA_VERSION,
    approved as approved_source,
    member as source_member,
    root as source_root,
)
from sec_engine_api import approved_release, artifact, entry, root
from temporal_core import normalize_date_only, parse_instant

PATH = "/private/v1/equity-claims"
PARAMETERS = {
    "cik",
    "release_id",
    "as_of",
    "expected_release_id",
    "expected_source_release_id",
    "source_release_id",
    "recipient",
}
MAX_RESPONSE = 2 * 1048576

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso(epoch_ns):
    seconds, nanos = divmod(epoch_ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return base.strftime("%Y-%m-%dT%H:%M:%S") + (".%09d" % nanos).rstrip("0") + "Z"


def fail(code, status=400):
    raise PrivateFinancialsError(code, code, status)


def parameters(url):
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    if set(query) - PARAMETERS or any(len(values) != 1 for values in query.values()):
        fail("INVALID_PARAMETER")
    return {key: values[0] for key, values in query.items()}


def resolve_cutoff(value):
    """One documented convention for the valuation cutoff.

    A date-only request is an end-of-day request: it resolves to the last instant of that
    UTC day, so filings dated that day are eligible. An explicit timestamp is honoured as
    given, and because a filing date carries no acceptance time it only counts once its own
    day has ended, so an intraday instant never picks up a same-day filing.
    """
    if DATE_ONLY.fullmatch(value):
        instant = normalize_date_only(value)
        day_complete = True
    else:
        instant = parse_instant(value)
        day_complete = instant.epoch_ns >= end_of_day_utc(
            date.fromisoformat(instant.iso_utc[:10])
        )
    valuation_date = date.fromisoformat(instant.iso_utc[:10])
    return instant, valuation_date, day_complete


def source_ceiling(maximum_as_of, date_marker=None):
    """The last instant a release can be asked about, from its date-level marker.

    Every marker in this project that describes a release's reach is a *date*: ``date_to``,
    a snapshot date, or ``snapshot_date + "T00:00:00Z"``. Reading such a marker as midnight
    would refuse the snapshot day itself, which is inside the release, so a date-level
    marker resolves to the end of that UTC day, exactly as a date-only request does. An
    explicit timestamp marker is honoured as given.

    ``date_marker`` lets a release state the date directly (the companion manifest does),
    and a marker that is exactly midnight UTC is read as that date's day-level reach rather
    than as a midnight instant.
    """
    if isinstance(date_marker, str) and DATE_ONLY.fullmatch(date_marker):
        return end_of_day_utc(date.fromisoformat(date_marker))
    if not isinstance(maximum_as_of, str):
        fail("SOURCE_CONTEXT_INVALID", 503)
    if DATE_ONLY.fullmatch(maximum_as_of):
        return end_of_day_utc(date.fromisoformat(maximum_as_of))
    instant = parse_instant(maximum_as_of)
    if instant.epoch_ns % 86_400_000_000_000 == 0:
        day = date.fromisoformat(instant.iso_utc[:10])
        return end_of_day_utc(day)
    return instant.epoch_ns


def cutoff_report(value, instant, valuation_date, day_complete):
    return {
        "requested": value,
        "precision": instant.precision,
        "policy": AVAILABILITY_POLICY if day_complete and DATE_ONLY.fullmatch(value) else "EXPLICIT_INSTANT",
        "resolved_utc": instant.iso_utc,
        "valuation_date": valuation_date.isoformat(),
        "day_complete": day_complete,
        "source_availability_rule": AVAILABILITY_RULE,
    }


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
        instant, valuation_date, day_complete = resolve_cutoff(p["as_of"])
    except (ValueError, TypeError):
        fail("INVALID_AVAILABILITY_CUTOFF", 400)
    if instant.epoch_ns > parse_instant(datetime.now(timezone.utc).isoformat()).epoch_ns:
        fail("CUTOFF_EXCEEDS_SOURCE_RELEASE", 422)
    manifest = await root(env)
    if p.get("release_id"):
        manifest = await approved_release(env, manifest, p["release_id"])
    if p.get("expected_release_id", manifest["release_id"]) != manifest["release_id"]:
        fail("SOURCE_VERSION_CHANGED", 409)
    item = await entry(env, manifest, cik)
    if item.get("maximum_as_of") and instant.epoch_ns > source_ceiling(item["maximum_as_of"]):
        fail("CUTOFF_EXCEEDS_SOURCE_RELEASE", 422)
    source = await source_root(env)
    if p.get("source_release_id"):
        source = await approved_source(env, source, p["source_release_id"])
    if p.get("expected_source_release_id", source["release_id"]) != source["release_id"]:
        fail("SOURCE_RELEASE_CHANGED", 409)
    ceiling = source_ceiling(source["maximum_as_of"], source.get("maximum_as_of_date"))
    if instant.epoch_ns > ceiling:
        fail("CUTOFF_EXCEEDS_SOURCE_RELEASE", 422)
    data, member = await source_member(env, source, cik)
    engine_member_sha256 = (item.get("materialized") or {}).get("member_sha256")
    if engine_member_sha256 and engine_member_sha256 != member["sha256"]:
        # Two independently published releases disagree about the bytes behind this filer.
        fail("SOURCE_BUNDLE_MEMBER_MISMATCH", 409)
    try:
        facts = restrict_to_available(data["facts"], instant.epoch_ns)
        claims = build_claims(facts, valuation_date, cik=cik)
    except ValueError as error:
        fail(str(error), 422)
    result = dict(
        claims,
        release_id=manifest["release_id"],
        registry_sha256=manifest["registry_sha256"],
        manifest_sha256=manifest.get(
            "_manifest_sha256", getattr(env, "SEC_ENGINE_MANIFEST_SHA256", None)
        ),
        member_sha256=member["sha256"],
        source_release_id=source["release_id"],
        source_schema_version=SOURCE_SCHEMA_VERSION,
        source_manifest_sha256=source["_manifest_sha256"],
        source_member_sha256=member["sha256"],
        company_label=data.get("entityName"),
        cutoff=dict(
            cutoff_report(p["as_of"], instant, valuation_date, day_complete),
            source_ceiling_utc=_iso(ceiling),
        ),
        source_bundle={
            "issuer": {
                "cik": cik,
                "entity_name": data.get("entityName"),
                "engine_label": item.get("label"),
            },
            "engine_release": {
                "release_id": manifest["release_id"],
                "manifest_sha256": manifest.get("_manifest_sha256"),
                "registry_sha256": manifest["registry_sha256"],
                "normalized_member_key": (item.get("materialized") or {}).get("key"),
                "normalized_member_sha256": (item.get("materialized") or {}).get("sha256"),
                "raw_archive_member_sha256": engine_member_sha256,
                "source_eligibility": item.get("source_eligibility"),
                "maximum_as_of": item.get("maximum_as_of") or manifest["maximum_as_of"],
            },
            "cutoff_ceiling_utc": _iso(ceiling),
            "companyfacts_release": {
                "release_id": source["release_id"],
                "schema_version": source["schema_version"],
                "manifest_sha256": source["_manifest_sha256"],
                "member_key": member["key"],
                "storage": member.get("storage", "IMMUTABLE_COMPANION_MEMBER"),
                "member_sha256": member["sha256"],
                "member_bytes": member["bytes"],
                "archive_member": member.get("archive_entry"),
                "snapshot_date": (source.get("source") or {}).get("snapshot_date"),
                "maximum_as_of": source["maximum_as_of"],
                "rights": source["rights"],
            },
            "reconciliation": {
                "status": "MEMBER_SHA256_AGREES_WITH_ENGINE_PINNED_MEMBER"
                if engine_member_sha256 == member["sha256"]
                else "ENGINE_RELEASE_DOES_NOT_PIN_A_RAW_MEMBER;COMPANION_VOUCHES_FOR_ITSELF",
                "engine_raw_member_sha256": engine_member_sha256,
                "companion_member_sha256": member["sha256"],
                "bulk_archive": (source.get("source") or {}).get("etag"),
                "bulk_archive_declared_sha256": (source.get("source") or {}).get("declared_sha256"),
            },
            "extraction": {
                "claims_schema_version": VERSION,
                "availability_policy": AVAILABILITY_POLICY,
                "availability_applied_at_serving_boundary": True,
            },
        },
        audit=audit,
        provenance={
            "engine": "NATIVE_IMMUTABLE_SEC_EQUITY_CLAIMS",
            "source": "SEC_COMPANY_FACTS_COMPANION_PINNED_ARCHIVE_MEMBER",
            "extraction_version": VERSION,
            "release_id": manifest["release_id"],
            "registry_sha256": manifest["registry_sha256"],
            "source_release_id": source["release_id"],
            "member": member["key"],
            "engine_member": (item.get("materialized") or {}).get("key"),
            "source_lineage": {
                "rights": "PUBLIC_SEC_ONLY",
                "licensed_sources_used": False,
            },
            "archive_bytes_read_interactively": member.get("_archive_bytes_read", 0),
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
