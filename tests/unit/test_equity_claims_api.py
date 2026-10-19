"""Route, pinning and authentication tests for the private equity-claims endpoint.

The endpoint must serve a versioned claims contract read only from the pinned SEC
engine archive, must refuse any request it cannot authenticate, and must not let the
scoped internal service credential widen access to the bearer-only private API.
"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from equity_claims import VERSION
from equity_claims_api import PATH, handle
from private_financials import PrivateFinancialsError
from private_http import SERVICE_CALLERS, authorize
from sec_engine import VERSION as ENGINE_VERSION, encoded

CIK = "0001652044"
OTHER_CIK = "0000320193"
RELEASE = "b" * 64
REGISTRY_SHA = "c" * 64
PREFIX = "gold/sec-engine/releases/" + RELEASE + "/"
MANIFEST_KEY = PREFIX + "manifest.json"
SHARD_KEY = PREFIX + "shard-" + CIK[-2:] + ".json"
MEMBER_KEY = PREFIX + CIK + ".json"
AS_OF = "2025-08-01T00:00:00Z"
ENTRY_MAXIMUM_AS_OF = "2025-09-30T00:00:00Z"
PRIVATE_TOKEN = "owned-test-token"
SERVICE_TOKEN = "internal-service-token"
CALLER = "dcf-valuation-engine"

# Values mirror a real filer's reported preferred carrying value, noncontrolling
# interest in variable interest entities, borrowings and share count.
PREFERRED_CARRYING = 18_023_000_000
NONCONTROLLING_VIE = 7_100_000_000
CASH = 55_911_000_000
DEBT_CURRENT = 1_999_000_000
DEBT_NONCURRENT = 98_165_000_000
FINANCE_LEASE_TOTAL = 2_590_000_000
COMMON_SHARES = 12_230_000_000
PREFERRED_SHARES = 19_000_000
DEBT_TOTAL = DEBT_CURRENT + DEBT_NONCURRENT
DEDUCT_TOTAL = DEBT_TOTAL + FINANCE_LEASE_TOTAL + PREFERRED_CARRYING + NONCONTROLLING_VIE
ADD_TOTAL = CASH + CASH  # cash is also the only nonoperating asset in this fixture

# The companion Company Facts release: a second immutable release, published from the same
# pinned bulk archive, that carries the raw member the engine release pinned the digest of.
SOURCE_RELEASE = "e" * 64
SOURCE_PREFIX = "gold/sec-companyfacts/releases/" + SOURCE_RELEASE + "/"
SOURCE_MANIFEST_KEY = SOURCE_PREFIX + "manifest.json"
SOURCE_MEMBER_KEY = SOURCE_PREFIX + "companies/" + CIK + ".json"
SOURCE_SCHEMA = "sec-companyfacts-source-v1"
SOURCE_MAXIMUM_AS_OF_DATE = "2025-09-30"
NORMALIZED_MEMBER_SHA = "f" * 64


def fact(value, end="2025-06-30", filed="2025-07-25", form="10-Q", accn="0001652044-25-000050"):
    return {"val": value, "end": end, "filed": filed, "form": form, "accn": accn}


def facts_body(cik=CIK, **overrides):
    us_gaap = {
        "ConvertiblePreferredStockNonredeemableOrRedeemableIssuerOptionValue": {
            "units": {"USD": [fact(PREFERRED_CARRYING)]}
        },
        "PreferredStockSharesOutstanding": {"units": {"shares": [fact(PREFERRED_SHARES)]}},
        "NoncontrollingInterestInVariableInterestEntity": {
            "units": {"USD": [fact(NONCONTROLLING_VIE)]}
        },
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [fact(CASH)]}},
        "LongTermDebtCurrent": {"units": {"USD": [fact(DEBT_CURRENT)]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [fact(DEBT_NONCURRENT)]}},
        "FinanceLeaseLiability": {"units": {"USD": [fact(FINANCE_LEASE_TOTAL)]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [fact(COMMON_SHARES)]}},
    }
    us_gaap.update(overrides)
    return {"cik": int(cik), "entityName": "Test Filer Inc.", "facts": {"us-gaap": us_gaap, "dei": {}}}


class Bucket:
    def __init__(self, bodies):
        self.bodies = bodies
        self.reads = []

    async def get(self, key):
        self.reads.append(key)
        raw = self.bodies.get(key)
        if raw is None:
            return None

        async def text():
            return raw.decode()

        return SimpleNamespace(size=len(raw), text=text)


def fixture(
    *,
    member=None,
    cik=CIK,
    maximum_as_of=ENTRY_MAXIMUM_AS_OF,
    service_token=None,
    source_member=None,
    source_release_id=SOURCE_RELEASE,
    source_maximum_as_of_date=SOURCE_MAXIMUM_AS_OF_DATE,
    engine_pins_raw_member=True,
    engine_member_sha256=None,
    source_engine_member_sha256=None,
    source_policy=None,
):
    """One eligible filer in both releases: the engine entry and the Company Facts member.

    ``member`` is the engine release's normalized projection (the route never reads it),
    and ``source_member`` is the raw Company Facts member the claims are extracted from.
    """
    member = facts_body(cik) if member is None else member
    source_member = member if source_member is None else source_member
    member_body = encoded(member)
    source_body = encoded(source_member)
    raw_sha = hashlib.sha256(source_body).hexdigest()
    materialized = {
        "key": MEMBER_KEY,
        "sha256": NORMALIZED_MEMBER_SHA,
        "bytes": len(member_body),
    }
    if engine_pins_raw_member:
        materialized["member_sha256"] = engine_member_sha256 or raw_sha
        materialized["source_release_id"] = RELEASE
    item = {
        "cik": cik,
        "label": "Test Filer Inc.",
        "source_eligibility": {"classification": "ELIGIBLE_US_PERIODIC_SOURCE", "eligible": True},
        "materialized": materialized,
    }
    if maximum_as_of is not None:
        item["maximum_as_of"] = maximum_as_of
    shard_body = encoded({cik: item})
    manifest = {
        "schema_version": ENGINE_VERSION,
        "release_id": RELEASE,
        "registry_sha256": REGISTRY_SHA,
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "maximum_as_of": ENTRY_MAXIMUM_AS_OF,
        "date_from": "2015-01-01",
        "date_to": "2025-07-25",
        "shards": {cik[-2:]: {"key": SHARD_KEY, "sha256": hashlib.sha256(shard_body).hexdigest()}},
    }
    manifest_body = encoded(manifest)
    source_manifest = {
        "schema_version": SOURCE_SCHEMA,
        "release_id": source_release_id,
        "index_sha256": source_release_id,
        "extraction_version": "sec-companyfacts-member-v1",
        "scope": "CONSOLIDATED_PUBLIC_SEC_COMPANY_FACTS",
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "source": {
            "bucket": "financial-data-wrds-private",
            "key": "sec/bulk/sec-nightly-20250925/companyfacts.zip",
            "etag": "abc-1",
            "declared_sha256": "d" * 64,
            "snapshot_date": source_maximum_as_of_date,
        },
        "engine_releases": {"manifest": RELEASE, "raw_member_pins": [RELEASE]},
        "date_from": "2015-01-01",
        "date_to": source_maximum_as_of_date,
        "maximum_as_of": source_maximum_as_of_date + "T00:00:00Z",
        "maximum_as_of_date": source_maximum_as_of_date,
        "member_count": 1,
        "members": {
            cik: {
                "key": SOURCE_MEMBER_KEY,
                "sha256": raw_sha,
                "bytes": len(source_body),
                "engine_member_sha256": source_engine_member_sha256
                or materialized.get("member_sha256", raw_sha),
                "engine_release_id": RELEASE,
                "engine_member_key": MEMBER_KEY,
                "archive_entry": {"name": "CIK" + cik + ".json", "crc": 1},
                "label": "Test Filer Inc.",
                "entity_name": "Test Filer Inc.",
            }
        },
        "previous_releases": {},
        "policy": "IMMUTABLE_COMPANION_SOURCE;DUAL_RELEASE_PINNING",
        "reconciliation": "Every companion member digest equals the engine pin.",
    }
    if source_policy:
        source_manifest.update(source_policy)
    source_body_encoded = encoded(source_manifest)
    bucket = Bucket(
        {
            MANIFEST_KEY: manifest_body,
            SHARD_KEY: shard_body,
            MEMBER_KEY: member_body,
            SOURCE_MANIFEST_KEY: source_body_encoded,
            SOURCE_MEMBER_KEY: source_body,
        }
    )
    env = SimpleNamespace(
        MARKET_DATA=bucket,
        SEC_ENGINE_MANIFEST_KEY=MANIFEST_KEY,
        SEC_ENGINE_MANIFEST_SHA256=hashlib.sha256(manifest_body).hexdigest(),
        SEC_COMPANYFACTS_MANIFEST_KEY=SOURCE_MANIFEST_KEY,
        SEC_COMPANYFACTS_MANIFEST_SHA256=hashlib.sha256(source_body_encoded).hexdigest(),
        ZION_PRIVATE_API_TOKEN=PRIVATE_TOKEN,
        ZION_SERVICE_TOKEN=service_token,
    )
    return env, bucket


def request(*, cik=CIK, as_of=AS_OF, token=PRIVATE_TOKEN, method="GET", extra="", headers=None):
    merged = {} if token is None else {"authorization": "Bearer " + token}
    if headers:
        merged.update(headers)
    return SimpleNamespace(
        url="https://private" + PATH + "?cik=" + cik + "&as_of=" + as_of + extra,
        method=method,
        headers=merged,
    )


def claim(result, name):
    return result["claims"][name]


def test_source_pinned_claims_response_with_private_bearer():
    env, bucket = fixture()
    result, status = asyncio.run(handle(env, request()))
    assert status == 200, result
    assert result["schema_version"] == VERSION
    assert result["rights"] == "PUBLIC_SEC_ONLY"
    assert result["release_id"] == RELEASE
    assert result["registry_sha256"] == REGISTRY_SHA
    assert result["manifest_sha256"] == env.SEC_ENGINE_MANIFEST_SHA256
    # The member digest names the Company Facts bytes the claims were extracted from, and
    # both releases must name the same bytes for the bundle to be usable.
    assert result["member_sha256"] == hashlib.sha256(bucket.bodies[SOURCE_MEMBER_KEY]).hexdigest()
    assert result["company_label"] == "Test Filer Inc."
    assert result["valuation_date"] == "2025-08-01"
    assert result["source_release_id"] == SOURCE_RELEASE
    assert result["source_schema_version"] == SOURCE_SCHEMA
    assert result["source_manifest_sha256"] == env.SEC_COMPANYFACTS_MANIFEST_SHA256
    assert result["source_bundle"]["reconciliation"] == {
        "status": "MEMBER_SHA256_AGREES_WITH_ENGINE_PINNED_MEMBER",
        "engine_raw_member_sha256": result["member_sha256"],
        "companion_member_sha256": result["member_sha256"],
        "bulk_archive": "abc-1",
        "bulk_archive_declared_sha256": "d" * 64,
    }
    assert result["source_bundle"]["engine_release"]["release_id"] == RELEASE
    assert result["source_bundle"]["engine_release"]["normalized_member_sha256"] == NORMALIZED_MEMBER_SHA
    assert result["source_bundle"]["companyfacts_release"]["release_id"] == SOURCE_RELEASE
    assert result["source_bundle"]["companyfacts_release"]["member_sha256"] == result["member_sha256"]
    assert result["source_bundle"]["issuer"]["cik"] == CIK
    assert result["source_bundle"]["extraction"]["availability_policy"] == "DATE_ONLY_END_OF_DAY_UTC_V1"
    assert result["provenance"]["extraction_version"] == VERSION
    assert result["provenance"]["member"] == SOURCE_MEMBER_KEY
    assert result["provenance"]["engine_member"] == MEMBER_KEY
    assert result["provenance"]["source_release_id"] == SOURCE_RELEASE
    assert result["provenance"]["archive_bytes_read_interactively"] == 0
    assert result["provenance"]["source_lineage"] == {
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_used": False,
    }
    assert result["audit"] == {
        "authenticated_via": "BEARER",
        "caller": "private_bearer_client",
        "scope": "PRIVATE_API",
    }
    assert result["limits"]["max_age_days"] == 400
    # Only the two pinned releases are read, the engine projection is never loaded, and
    # nothing is fetched at request time.
    assert bucket.reads == [MANIFEST_KEY, SHARD_KEY, SOURCE_MANIFEST_KEY, SOURCE_MEMBER_KEY]


def test_claims_reflect_reported_filing_facts():
    env, _ = fixture()
    result, status = asyncio.run(handle(env, request()))
    assert status == 200, result
    assert claim(result, "preferred_equity")["value_decimal"] == str(PREFERRED_CARRYING)
    assert claim(result, "preferred_equity")["evidence_state"] == "REPORTED"
    assert claim(result, "preferred_equity")["shares_outstanding_decimal"] == str(PREFERRED_SHARES)
    assert claim(result, "noncontrolling_interests")["evidence_state"] == "REPORTED"
    assert claim(result, "noncontrolling_interests")["value_decimal"] == str(NONCONTROLLING_VIE)
    assert (
        claim(result, "noncontrolling_interests")["classification"]
        == "VIE_DISCLOSURE_NONCONTROLLING_INTEREST"
    )
    assert claim(result, "noncontrolling_interests")["redeemable_subset"]["evidence_state"] == "UNAVAILABLE"
    assert claim(result, "debt")["value_decimal"] == str(DEBT_TOTAL)
    assert claim(result, "finance_leases")["value_decimal"] == str(FINANCE_LEASE_TOTAL)
    assert claim(result, "cash")["value_decimal"] == str(CASH)
    assert claim(result, "nonoperating_assets")["value_decimal"] == str(CASH)
    assert claim(result, "common_shares")["value_decimal"] == str(COMMON_SHARES)
    assert claim(result, "common_shares")["basis"] == "BALANCE_SHEET_ALL_CLASSES"
    assert claim(result, "operating_leases")["evidence_state"] == "REVIEWED_ABSENT"
    aggregate = result["aggregate_claims"]
    assert aggregate["status"] == "AVAILABLE"
    assert aggregate["deduct_claims_decimal"] == str(DEDUCT_TOTAL)
    assert aggregate["add_claims_decimal"] == str(ADD_TOTAL)
    assert aggregate["deduct_components"]["debt"] == str(DEBT_TOTAL)
    assert aggregate["deduct_components"]["noncontrolling_interests"] == str(NONCONTROLLING_VIE)


def test_every_claim_carries_evidence_state_digest_and_filing_identity():
    env, _ = fixture()
    result, _ = asyncio.run(handle(env, request()))
    assert result["evidence_states"] == [
        "REPORTED",
        "DERIVED_VERIFIED",
        "REVIEWED_ABSENT",
        "ASSUMED",
        "CONFLICTING",
        "UNAVAILABLE",
    ]
    for name in (
        "debt",
        "finance_leases",
        "operating_leases",
        "cash",
        "nonoperating_assets",
        "preferred_equity",
        "noncontrolling_interests",
        "common_shares",
    ):
        value = claim(result, name)
        assert value["evidence_state"] in result["evidence_states"], name
        assert value["evidence_digest"], name
        assert value["measure"] and value["selection_rule"], name
        for source in value["sources"]:
            assert source["concept"].startswith("us-gaap:") or source["concept"].startswith("dei:")
            assert source["accession"]
            assert source["form"] in ("10-K", "10-K/A", "10-Q", "10-Q/A")
            assert source["filed"] and source["period_end"] and source["evidence_digest"]
            assert isinstance(source["reported_token"], str)
        if value["value_decimal"] is not None:
            assert value["sources"], name


def test_reviewed_absent_deduct_claim_is_a_verified_zero_not_a_missing_claim():
    env, _ = fixture(member=facts_body(FinanceLeaseLiability=None))
    result, status = asyncio.run(handle(env, request()))
    assert status == 200, result
    absence = claim(result, "finance_leases")
    assert absence["evidence_state"] == "REVIEWED_ABSENT"
    # No value, no sources, and an explicit statement that a missing concept is not a zero claim.
    assert absence["value_decimal"] is None
    assert absence["sources"] == []
    assert absence["verified_absent"] is True
    assert any("not evidence of a zero claim" in note for note in absence["notes"])
    # A reviewed-absent claim is a verified zero for the bridge, so the aggregate stays usable and
    # the absence is recorded by name rather than being silently dropped or treated as unknown.
    assert result["aggregate_claims"]["status"] == "AVAILABLE"
    assert result["aggregate_claims"]["verified_absent"] == ["finance_leases"]
    assert result["aggregate_claims"]["missing"] if "missing" in result["aggregate_claims"] else True


def test_claims_response_is_deterministic_for_a_pinned_release():
    env, _ = fixture()
    first, _ = asyncio.run(handle(env, request()))
    env, _ = fixture()
    second, _ = asyncio.run(handle(env, request()))
    assert first == second


def test_missing_or_wrong_bearer_is_rejected_without_reading_the_archive():
    env, bucket = fixture()
    for token in (None, "wrong-token"):
        result, status = asyncio.run(handle(env, request(token=token)))
        # This route accepts a scoped service credential as its second factor, so an
        # absent or wrong bearer with no usable service credential is refused rather
        # than served; the security promise is refusal with zero archive reads.
        assert status in (401, 503), result
        assert result["error"]["code"] in ("UNAUTHORIZED", "SERVICE_AUTH_UNCONFIGURED")
    assert bucket.reads == []


def test_wrong_bearer_is_unauthorized_where_the_service_scope_excludes_the_path():
    env, _ = fixture()
    with pytest.raises(PrivateFinancialsError) as error:
        authorize(env, request(token="wrong-token"), service_paths=())
    assert (error.value.status, error.value.code) == (401, "UNAUTHORIZED")


def test_scoped_service_credential_authenticates_the_equity_claims_route():
    env, _ = fixture(service_token=SERVICE_TOKEN)
    result, status = asyncio.run(
        handle(
            env,
            request(
                token=None,
                headers={"x-zion-service-token": SERVICE_TOKEN, "x-zion-service-caller": CALLER},
            ),
        )
    )
    assert status == 200, result
    assert CALLER in SERVICE_CALLERS
    assert result["audit"] == {
        "authenticated_via": "SERVICE_CREDENTIAL",
        "caller": CALLER,
        "scope": "SERVICE_BINDING_ROUTE_SCOPE",
        "authorized_paths": [PATH],
    }
    assert claim(result, "noncontrolling_interests")["value_decimal"] == str(NONCONTROLLING_VIE)


def test_service_credential_requires_an_approved_caller_and_the_configured_token():
    env, bucket = fixture(service_token=SERVICE_TOKEN)
    unapproved, status = asyncio.run(
        handle(
            env,
            request(
                token=None,
                headers={"x-zion-service-token": SERVICE_TOKEN, "x-zion-service-caller": "some-other-worker"},
            ),
        )
    )
    assert (status, unapproved["error"]["code"]) == (403, "SERVICE_CALLER_NOT_PERMITTED")
    wrong_token, status = asyncio.run(
        handle(
            env,
            request(
                token=None,
                headers={"x-zion-service-token": "not-the-configured-token", "x-zion-service-caller": CALLER},
            ),
        )
    )
    assert (status, wrong_token["error"]["code"]) == (401, "UNAUTHORIZED")
    # With no credential headers at all the token check rejects first with 401, before the
    # caller allowlist and before any archive read, so refusal never looks like an authorization.
    anonymous, status = asyncio.run(handle(env, request(token=None)))
    assert (status, anonymous["error"]["code"]) == (401, "UNAUTHORIZED")
    assert bucket.reads == []


def test_service_credential_does_not_widen_access_to_bearer_only_routes():
    env, _ = fixture(service_token=SERVICE_TOKEN)
    service_headers = {"x-zion-service-token": SERVICE_TOKEN, "x-zion-service-caller": CALLER}
    other_path = "https://private/private/v1/financial-history?cik=" + CIK
    with pytest.raises(PrivateFinancialsError) as error:
        authorize(
            env,
            SimpleNamespace(url=other_path, method="GET", headers=service_headers),
            service_paths=(PATH,),
        )
    assert (error.value.status, error.value.code) == (401, "UNAUTHORIZED")
    # The scoped credential is never accepted when no route scope is declared.
    with pytest.raises(PrivateFinancialsError) as unscoped:
        authorize(
            env,
            SimpleNamespace(url="https://private" + PATH, method="GET", headers=service_headers),
        )
    assert (unscoped.value.status, unscoped.value.code) == (401, "UNAUTHORIZED")
    # The bearer credential keeps full private-API scope on the same path, and the
    # scoped credential is accepted on the one route it was scoped to.
    assert (
        authorize(
            env,
            SimpleNamespace(
                url=other_path,
                method="GET",
                headers={"authorization": "Bearer " + PRIVATE_TOKEN},
            ),
            service_paths=(PATH,),
        )["authenticated_via"]
        == "BEARER"
    )
    assert (
        authorize(
            env,
            SimpleNamespace(url="https://private" + PATH, method="GET", headers=service_headers),
            service_paths=(PATH,),
        )["authenticated_via"]
        == "SERVICE_CREDENTIAL"
    )


def test_expected_release_id_mismatch_is_a_source_version_conflict():
    env, bucket = fixture()
    result, status = asyncio.run(handle(env, request(extra="&expected_release_id=" + "d" * 64)))
    assert (status, result["error"]["code"]) == (409, "SOURCE_VERSION_CHANGED")
    # The pinned manifest is read to learn the current release, and nothing else is read.
    assert bucket.reads == [MANIFEST_KEY]
    matching, status = asyncio.run(handle(env, request(extra="&expected_release_id=" + RELEASE)))
    assert status == 200, matching


def test_invalid_identifiers_parameters_and_methods_are_rejected_before_archive_access():
    env, bucket = fixture()
    cases = [
        (request(cik="1652044"), 400, "INVALID_CIK"),
        (request(cik=""), 400, "INVALID_CIK"),
        (request(as_of=""), 422, "CUTOFF_REQUIRED"),
        (request(as_of="2025-13-01"), 400, "INVALID_AVAILABILITY_CUTOFF"),
        (request(as_of="2025-08-01T00:00:00"), 400, "INVALID_AVAILABILITY_CUTOFF"),
        (request(as_of="not-an-instant"), 400, "INVALID_AVAILABILITY_CUTOFF"),
        (request(extra="&sql=SELECT+1"), 400, "INVALID_PARAMETER"),
        (request(extra="&cik=" + CIK), 400, "INVALID_PARAMETER"),
        (request(extra="&recipient=llm"), 403, "RECIPIENT_NOT_PERMITTED"),
        (request(method="POST"), 405, "READ_ONLY"),
    ]
    for request_object, expected_status, expected_code in cases:
        result, status = asyncio.run(handle(env, request_object))
        assert (status, result["error"]["code"]) == (expected_status, expected_code), request_object.url
    assert bucket.reads == []


def test_cik_absent_from_the_pinned_shard_is_not_found():
    env, bucket = fixture()
    result, status = asyncio.run(handle(env, request(cik=OTHER_CIK)))
    assert (status, result["error"]["code"]) == (404, "CIK_NOT_IN_SOURCE_ARCHIVE")
    assert bucket.reads == [MANIFEST_KEY]


def test_cutoffs_beyond_the_pinned_release_are_rejected():
    env, bucket = fixture()
    future, status = asyncio.run(handle(env, request(as_of="2099-01-01T00:00:00Z")))
    assert (status, future["error"]["code"]) == (422, "CUTOFF_EXCEEDS_SOURCE_RELEASE")
    assert bucket.reads == []
    beyond_entry, status = asyncio.run(handle(env, request(as_of="2025-10-01T00:00:00Z")))
    assert (status, beyond_entry["error"]["code"]) == (422, "CUTOFF_EXCEEDS_SOURCE_RELEASE")
    assert bucket.reads == [MANIFEST_KEY, SHARD_KEY]


def test_company_facts_member_identity_or_payload_mismatch_is_rejected():
    env, _ = fixture(member=facts_body(cik=OTHER_CIK))
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (503, "SOURCE_CONTEXT_INVALID")
    env, _ = fixture(member={"cik": int(CIK), "entityName": "Test Filer Inc."})
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (503, "SOURCE_CONTEXT_INVALID")


def test_tampered_archive_member_is_rejected_by_digest():
    env, bucket = fixture()
    bucket.bodies[SOURCE_MEMBER_KEY] = bucket.bodies[SOURCE_MEMBER_KEY].replace(
        b"7100000000", b"9999999999"
    )
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (409, "SOURCE_VERSION_CHANGED")


def test_company_facts_member_that_is_not_a_company_facts_document_is_rejected():
    env, _ = fixture(source_member={"cik": int(CIK), "entityName": "Test Filer Inc."})
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (503, "SOURCE_CONTEXT_INVALID")
    env, _ = fixture(source_member=facts_body(cik=OTHER_CIK))
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (503, "SOURCE_CONTEXT_INVALID")


def test_releases_that_pin_different_bytes_for_one_filer_are_a_conflict():
    env, _ = fixture(engine_member_sha256="9" * 64)
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (409, "SOURCE_BUNDLE_MEMBER_MISMATCH")


def test_companion_release_that_contradicts_its_own_engine_pin_is_refused():
    env, _ = fixture(source_engine_member_sha256="9" * 64)
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (409, "SOURCE_BUNDLE_MEMBER_MISMATCH")


def test_engine_entry_without_a_raw_member_pin_is_served_and_labelled():
    env, _ = fixture(engine_pins_raw_member=False)
    result, status = asyncio.run(handle(env, request()))
    assert status == 200, result
    assert result["source_bundle"]["reconciliation"]["status"] == (
        "ENGINE_RELEASE_DOES_NOT_PIN_A_RAW_MEMBER;COMPANION_VOUCHES_FOR_ITSELF"
    )
    assert result["source_bundle"]["reconciliation"]["engine_raw_member_sha256"] is None


def test_filer_absent_from_the_company_facts_release_is_not_found():
    env, bucket = fixture()
    source = json.loads(bucket.bodies[SOURCE_MANIFEST_KEY])
    source["members"] = {}
    body = encoded(source)
    bucket.bodies[SOURCE_MANIFEST_KEY] = body
    env.SEC_COMPANYFACTS_MANIFEST_SHA256 = hashlib.sha256(body).hexdigest()
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (503, "COMPANY_FACTS_SOURCE_INVALID")
    # A release with no members at all is an unusable release, not a missing filer.
    assert bucket.reads == [MANIFEST_KEY, SHARD_KEY, SOURCE_MANIFEST_KEY]


def test_filer_missing_from_a_populated_companion_release_is_not_found():
    env, bucket = fixture()
    source = json.loads(bucket.bodies[SOURCE_MANIFEST_KEY])
    source["members"] = {"0000000001": source["members"][CIK]}
    body = encoded(source)
    bucket.bodies[SOURCE_MANIFEST_KEY] = body
    env.SEC_COMPANYFACTS_MANIFEST_SHA256 = hashlib.sha256(body).hexdigest()
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (404, "COMPANY_FACTS_MEMBER_NOT_IN_SOURCE")


def test_unpinned_or_foreign_companion_manifest_key_is_refused():
    env, bucket = fixture()
    env.SEC_COMPANYFACTS_MANIFEST_KEY = "gold/sec-engine/releases/" + SOURCE_RELEASE + "/manifest.json"
    env.SEC_COMPANYFACTS_MANIFEST_SHA256 = env.SEC_COMPANYFACTS_MANIFEST_SHA256
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (503, "SOURCE_REFERENCE_INVALID")


def test_companion_release_policy_must_be_public_sec_only():
    env, _ = fixture(source_policy={"rights": "LICENSED_VENDOR_DATA"})
    result, status = asyncio.run(handle(env, request()))
    assert (status, result["error"]["code"]) == (403, "SOURCE_POLICY_NOT_PERMITTED")


def test_expected_source_release_id_mismatch_is_a_source_release_conflict():
    env, bucket = fixture()
    result, status = asyncio.run(
        handle(env, request(extra="&expected_source_release_id=" + "d" * 64))
    )
    assert (status, result["error"]["code"]) == (409, "SOURCE_RELEASE_CHANGED")
    matching, status = asyncio.run(
        handle(env, request(extra="&expected_source_release_id=" + SOURCE_RELEASE))
    )
    assert status == 200, matching


def test_named_source_release_must_be_an_approved_previous_release():
    env, _ = fixture()
    result, status = asyncio.run(handle(env, request(extra="&source_release_id=" + "a" * 64)))
    assert (status, result["error"]["code"]) == (404, "SOURCE_RELEASE_NOT_APPROVED")
    result, status = asyncio.run(handle(env, request(extra="&source_release_id=not-a-release")))
    assert (status, result["error"]["code"]) == (422, "INVALID_SOURCE_RELEASE_ID")


def test_approved_previous_source_release_can_be_replayed():
    env, bucket = fixture()
    older_id = "a" * 64
    older_prefix = "gold/sec-companyfacts/releases/" + older_id + "/"
    older_member_key = older_prefix + "companies/" + CIK + ".json"
    older_manifest_key = older_prefix + "manifest.json"
    current = json.loads(bucket.bodies[SOURCE_MANIFEST_KEY])
    older = dict(
        current,
        release_id=older_id,
        index_sha256=older_id,
        previous_releases={},
        members={CIK: dict(current["members"][CIK], key=older_member_key)},
    )
    older_body = encoded(older)
    current["previous_releases"] = {
        older_id: {"key": older_manifest_key, "sha256": hashlib.sha256(older_body).hexdigest()}
    }
    current_body = encoded(current)
    bucket.bodies[SOURCE_MANIFEST_KEY] = current_body
    bucket.bodies[older_manifest_key] = older_body
    bucket.bodies[older_member_key] = bucket.bodies[SOURCE_MEMBER_KEY]
    env.SEC_COMPANYFACTS_MANIFEST_SHA256 = hashlib.sha256(current_body).hexdigest()
    live, status = asyncio.run(handle(env, request()))
    assert status == 200 and live["source_release_id"] == SOURCE_RELEASE
    replayed, status = asyncio.run(
        handle(env, request(extra="&source_release_id=" + older_id))
    )
    assert status == 200, replayed
    assert replayed["source_release_id"] == older_id
    # The claims and the engine pin are identical; only the named source release differs.
    assert replayed["claims"] == live["claims"]
    assert replayed["release_id"] == live["release_id"]
    assert replayed["source_manifest_sha256"] == hashlib.sha256(older_body).hexdigest()
    assert replayed["source_bundle"]["companyfacts_release"]["member_key"] == older_member_key


def test_cutoff_beyond_the_company_facts_snapshot_is_rejected():
    env, _ = fixture()
    result, status = asyncio.run(handle(env, request(as_of="2025-10-01")))
    assert (status, result["error"]["code"]) == (422, "CUTOFF_EXCEEDS_SOURCE_RELEASE")
    # The snapshot day itself is inside the release.
    inside, status = asyncio.run(handle(env, request(as_of=SOURCE_MAXIMUM_AS_OF_DATE)))
    assert status == 200, inside


def test_date_only_cutoff_is_an_end_of_day_request():
    env, _ = fixture()
    result, status = asyncio.run(handle(env, request(as_of="2025-08-01")))
    assert status == 200, result
    assert result["cutoff"] == {
        "requested": "2025-08-01",
        "precision": "date",
        "policy": "DATE_ONLY_END_OF_DAY_UTC_V1",
        "resolved_utc": "2025-08-01T23:59:59.999999999Z",
        "valuation_date": "2025-08-01",
        "day_complete": True,
        "source_availability_rule": result["cutoff"]["source_availability_rule"],
        "source_ceiling_utc": "2025-09-30T23:59:59.999999999Z",
    }
    assert result["valuation_date"] == "2025-08-01"
    # A filing dated the valuation day itself is available to an end-of-day request.
    assert result["claims"]["cash"]["value_decimal"] == str(CASH)


def test_same_day_filing_is_excluded_from_an_intraday_cutoff():
    same_day = facts_body(CashAndCashEquivalentsAtCarryingValue={
        "units": {"USD": [fact(CASH, filed="2025-08-01")]}
    })
    env, _ = fixture(source_member=same_day)
    end_of_day, status = asyncio.run(handle(env, request(as_of="2025-08-01")))
    assert status == 200, end_of_day
    assert end_of_day["claims"]["cash"]["evidence_state"] == "REPORTED"
    intraday, status = asyncio.run(handle(env, request(as_of="2025-08-01T12:00:00Z")))
    assert status == 200, intraday
    assert intraday["cutoff"]["day_complete"] is False
    assert intraday["cutoff"]["policy"] == "EXPLICIT_INSTANT"
    # The same-day filing is not assumed available mid-day, so the claim is classified
    # rather than served as current evidence.
    assert intraday["claims"]["cash"]["evidence_state"] != "REPORTED"
    assert intraday["valuation_date"] == "2025-08-01"


CURRENT_RELEASE = "a" * 64
CURRENT_MANIFEST_KEY = "gold/sec-engine/releases/" + CURRENT_RELEASE + "/manifest.json"


def historical_fixture(**kwargs):
    """Promote a separate root which explicitly approves the original engine fixture."""
    env, bucket = fixture(**kwargs)
    historical = json.loads(bucket.bodies[MANIFEST_KEY])
    historical_sha = env.SEC_ENGINE_MANIFEST_SHA256
    current = dict(
        historical,
        release_id=CURRENT_RELEASE,
        registry_sha256="1" * 64,
        shards={},
        previous_releases={RELEASE: {"key": MANIFEST_KEY, "sha256": historical_sha}},
    )
    current_body = encoded(current)
    bucket.bodies[CURRENT_MANIFEST_KEY] = current_body
    env.SEC_ENGINE_MANIFEST_KEY = CURRENT_MANIFEST_KEY
    env.SEC_ENGINE_MANIFEST_SHA256 = hashlib.sha256(current_body).hexdigest()
    return env, bucket


def historical_pins():
    return "&release_id=" + RELEASE + "&expected_release_id=" + RELEASE


def test_approved_historical_engine_release_preserves_selected_identity_and_current_pointer():
    env, bucket = historical_fixture()
    original_bodies = dict(bucket.bodies)
    result, status = asyncio.run(handle(env, request(extra=historical_pins())))
    assert status == 200, result
    historical_sha = hashlib.sha256(bucket.bodies[MANIFEST_KEY]).hexdigest()
    assert result["release_id"] == RELEASE
    assert result["registry_sha256"] == REGISTRY_SHA
    assert result["manifest_sha256"] == historical_sha
    assert result["source_bundle"]["engine_release"]["manifest_sha256"] == historical_sha
    assert result["source_release_id"] == SOURCE_RELEASE
    assert claim(result, "debt")["value_decimal"] == str(DEBT_TOTAL)
    assert bucket.reads == [
        CURRENT_MANIFEST_KEY, MANIFEST_KEY, SHARD_KEY, SOURCE_MANIFEST_KEY, SOURCE_MEMBER_KEY
    ]
    assert bucket.bodies == original_bodies
    assert env.SEC_ENGINE_MANIFEST_KEY == CURRENT_MANIFEST_KEY


@pytest.mark.parametrize("release_id,status,code", [
    ("d" * 64, 404, "RELEASE_NOT_APPROVED"),
    ("../../manifest.json", 422, "INVALID_RELEASE_ID"),
])
def test_historical_engine_selection_rejects_unapproved_or_invalid_release(release_id, status, code):
    env, bucket = historical_fixture()
    result, actual_status = asyncio.run(handle(env, request(extra="&release_id=" + release_id)))
    assert (actual_status, result["error"]["code"]) == (status, code)
    assert bucket.reads == [CURRENT_MANIFEST_KEY]


def test_historical_engine_selection_checks_expected_pin_after_selecting_release():
    env, bucket = historical_fixture()
    result, status = asyncio.run(handle(env, request(
        extra="&release_id=" + RELEASE + "&expected_release_id=" + CURRENT_RELEASE
    )))
    assert (status, result["error"]["code"]) == (409, "SOURCE_VERSION_CHANGED")
    assert bucket.reads == [CURRENT_MANIFEST_KEY, MANIFEST_KEY]


def test_historical_engine_selection_rejects_tampered_approved_manifest():
    env, bucket = historical_fixture()
    bucket.bodies[MANIFEST_KEY] += b" "
    result, status = asyncio.run(handle(env, request(extra=historical_pins())))
    assert (status, result["error"]["code"]) == (409, "SOURCE_VERSION_CHANGED")


def test_historical_engine_selection_preserves_public_sec_policy():
    env, bucket = historical_fixture()
    historical = json.loads(bucket.bodies[MANIFEST_KEY])
    historical["licensed_sources_allowed"] = True
    historical_body = encoded(historical)
    bucket.bodies[MANIFEST_KEY] = historical_body
    current = json.loads(bucket.bodies[CURRENT_MANIFEST_KEY])
    current["previous_releases"][RELEASE]["sha256"] = hashlib.sha256(historical_body).hexdigest()
    current_body = encoded(current)
    bucket.bodies[CURRENT_MANIFEST_KEY] = current_body
    env.SEC_ENGINE_MANIFEST_SHA256 = hashlib.sha256(current_body).hexdigest()
    result, status = asyncio.run(handle(env, request(extra=historical_pins())))
    assert (status, result["error"]["code"]) == (403, "SOURCE_POLICY_NOT_PERMITTED")


@pytest.mark.parametrize("kwargs,as_of,extra,status,code", [
    ({"engine_member_sha256": "0" * 64}, AS_OF, "", 409, "SOURCE_BUNDLE_MEMBER_MISMATCH"),
    ({}, AS_OF, "&expected_source_release_id=" + "0" * 64, 409, "SOURCE_RELEASE_CHANGED"),
    ({}, AS_OF, "&source_release_id=" + "0" * 64, 404, "SOURCE_RELEASE_NOT_APPROVED"),
    ({}, "2099-01-01", "", 422, "CUTOFF_EXCEEDS_SOURCE_RELEASE"),
    ({"maximum_as_of": "2025-07-01"}, AS_OF, "", 422, "CUTOFF_EXCEEDS_SOURCE_RELEASE"),
    ({"source_maximum_as_of_date": "2025-07-01"}, AS_OF, "", 422, "CUTOFF_EXCEEDS_SOURCE_RELEASE"),
    ({"source_member": facts_body(cik=OTHER_CIK)}, AS_OF, "", 503, "SOURCE_CONTEXT_INVALID"),
])
def test_historical_engine_selection_preserves_source_and_cutoff_guards(kwargs, as_of, extra, status, code):
    env, _ = historical_fixture(**kwargs)
    result, actual_status = asyncio.run(handle(env, request(as_of=as_of, extra=historical_pins() + extra)))
    assert (actual_status, result["error"]["code"]) == (status, code)


def test_historical_engine_selection_preserves_available_at_filtering():
    env, _ = historical_fixture()
    result, status = asyncio.run(handle(env, request(
        as_of="2025-07-25T12:00:00Z", extra=historical_pins()
    )))
    assert status == 200, result
    assert claim(result, "debt")["value_decimal"] is None
    assert claim(result, "debt")["sources"] == []


def test_historical_engine_selection_accepts_only_scoped_authenticated_service_caller():
    env, bucket = historical_fixture(service_token=SERVICE_TOKEN)
    result, status = asyncio.run(handle(env, request(
        token=None, extra=historical_pins(),
        headers={"x-zion-service-token": SERVICE_TOKEN, "x-zion-service-caller": "unknown"},
    )))
    assert (status, result["error"]["code"]) == (403, "SERVICE_CALLER_NOT_PERMITTED")
    assert bucket.reads == []
    result, status = asyncio.run(handle(env, request(
        token=None, extra=historical_pins(),
        headers={"x-zion-service-token": SERVICE_TOKEN, "x-zion-service-caller": CALLER},
    )))
    assert status == 200, result
    assert result["release_id"] == RELEASE
    assert result["audit"]["authorized_paths"] == [PATH]
