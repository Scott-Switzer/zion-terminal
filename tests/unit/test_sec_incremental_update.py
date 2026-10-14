import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from sec_engine import history
from sec_incremental_update import SecFetcher, build_candidate, publish
from test_sec_history import fact, source

CIK = "0000320193"
WHEN = "2025-01-01T00:00:00Z"
PREVIOUS = {
    "release_id": "a" * 64,
    "registry_sha256": "b" * 64,
    "rights": "PUBLIC_SEC_ONLY",
    "licensed_sources_allowed": False,
}


def raw(rows):
    return json.dumps(source(rows)).encode()


def build(body, old=None, **kw):
    return build_candidate(
        PREVIOUS, json.dumps(PREVIOUS).encode(), {CIK: (body, {}, old)}, kw.get("when", WHEN)
    )


def result(objects, as_of):
    data = json.loads(objects["companies/" + CIK + ".json"])
    return history(
        data,
        metrics=["revenue"],
        frequency="annual",
        date_from="2020-01-01",
        date_to="2021-01-01",
        as_of=as_of,
    )


def test_two_revisions_preserve_exact_value_and_point_in_time():
    first = fact(val="12345678901234567890.123456789", filed="2021-02-01")
    second = fact(val="12345678901234567891.123456789", filed="2021-03-01", accn="b")
    manifest, objects = build(raw([first, second]), raw([first]))
    assert (
        result(objects, "2021-02-15T00:00:00Z")["observations"][0]["value_decimal"] == first["val"]
    )
    assert result(objects, WHEN)["observations"][0]["value_decimal"] == second["val"]
    assert manifest["production_pointer_writes"] == 0
    assert len(manifest["companies"][CIK]["added_source_ids"]) == 1


def test_deleted_context_cannot_resurrect_old_snapshot():
    manifest, objects = build(raw([]), raw([fact()]))
    assert not result(objects, WHEN)["observations"]
    assert len(manifest["companies"][CIK]["removed_source_ids"]) == 1
    assert json.loads(objects["companies/" + CIK + ".json"])["observations"] == []


def test_null_revision_is_retained_without_old_value_fallback():
    body = raw([fact(), fact(val=None, filed="2021-03-01", accn="b")])
    _, objects = build(body)
    assert not result(objects, WHEN)["observations"]
    assert result(objects, WHEN)["coverage_gaps"]


def test_unchanged_source_is_idempotent_and_skipped():
    body = raw([fact()])
    manifest, objects = build(body, body)
    assert not manifest["companies"]
    assert set(objects) == {"manifest.json"}
    assert build(body, body) == (manifest, objects)


def test_future_availability_not_served_even_with_new_raw_snapshot():
    _, objects = build(raw([fact(filed="2024-12-31")]), when="2024-12-31T12:00:00Z")
    assert not json.loads(objects["companies/" + CIK + ".json"])["observations"]


def test_wrong_cik_and_private_manifest_are_denied():
    invalid = source([fact()])
    invalid["cik"] = 1
    with pytest.raises(Exception, match="SOURCE_IDENTITY_MISMATCH"):
        build(json.dumps(invalid).encode())
    private = {**PREVIOUS, "licensed_sources_allowed": True}
    with pytest.raises(ValueError, match="PUBLIC_SEC_ONLY_REQUIRED"):
        build_candidate(private, b"{}", {CIK: (raw([]), {}, None)}, WHEN)


def test_fetch_requires_contact_and_fixed_cik_endpoints():
    with pytest.raises(ValueError, match="USER_AGENT"):
        SecFetcher("generic bot")
    client = SecFetcher("Zion Research operator@example.com")
    with pytest.raises(ValueError, match="INVALID_SEC_RESOURCE"):
        client.get("../secret")


def test_create_only_publication_verifies_existing_bytes():
    import io

    manifest, objects = build(raw([fact()]))

    class Store:
        class exceptions:
            class ClientError(Exception):
                pass

        def __init__(self):
            self.rows = {}

        def put_object(self, **kw):
            assert kw["IfNoneMatch"] == "*"
            assert "CURRENT" not in kw["Key"]
            self.rows.setdefault(kw["Key"], kw["Body"])

        def get_object(self, **kw):
            return {"Body": io.BytesIO(self.rows[kw["Key"]])}

    store = Store()
    assert publish(store, manifest, objects)["objects_verified"] == 3
    assert publish(store, manifest, objects)["objects_verified"] == 3
    store.rows[next(iter(store.rows))] = b"corrupt"
    with pytest.raises(ValueError, match="IMMUTABLE_OBJECT_CONFLICT"):
        publish(store, manifest, objects)


def test_decimal_json_token_is_preserved():
    body = raw([fact(val=1)]).replace(b'"val": 1', b'"val": 1.123456789123456789')
    _, objects = build(body)
    value = result(objects, WHEN)["observations"][0]["value_decimal"]
    assert Decimal(value) == Decimal("1.123456789123456789")


def test_conditional_fetch_304_uses_etag_without_body(monkeypatch):
    from urllib.error import HTTPError

    client = SecFetcher("Zion Research operator@example.com")
    seen = []

    def request(req, timeout):
        seen.append(req)
        raise HTTPError(req.full_url, 304, "Not modified", {}, None)

    monkeypatch.setattr(client.opener, "open", request)
    body, metadata = client.get(CIK, etag='"source-version"')
    assert body is None and metadata["http_status"] == 304
    assert seen[0].get_header("If-none-match") == '"source-version"'
    assert seen[0].full_url.startswith("https://data.sec.gov/api/xbrl/companyfacts/")


def test_access_denial_is_not_retried_or_redirected(monkeypatch):
    from urllib.error import HTTPError

    client = SecFetcher("Zion Research operator@example.com")
    requests = []

    def request(req, timeout):
        requests.append(req)
        raise HTTPError(req.full_url, 403, "Denied", {}, None)

    monkeypatch.setattr(client.opener, "open", request)
    with pytest.raises(HTTPError):
        client.get(CIK)
    assert len(requests) == 1


def test_candidate_cutoff_never_exceeds_individual_retrieval():
    previous_body = json.dumps(PREVIOUS).encode()
    metadata = {"fetched_at": "2024-12-31T12:00:00Z"}
    manifest, objects = build_candidate(
        PREVIOUS,
        previous_body,
        {CIK: (raw([fact(filed="2024-12-31")]), metadata, None)},
        WHEN,
    )
    assert manifest["companies"][CIK]["maximum_as_of"] == "2024-12-31T12:00:00+00:00"
    assert not json.loads(objects["companies/" + CIK + ".json"])["observations"]


def test_missing_entitlement_boolean_fails_closed():
    private = {k: v for k, v in PREVIOUS.items() if k != "licensed_sources_allowed"}
    with pytest.raises(ValueError, match="PUBLIC_SEC_ONLY_REQUIRED"):
        build_candidate(private, json.dumps(private).encode(), {CIK: (raw([]), {}, None)}, WHEN)
