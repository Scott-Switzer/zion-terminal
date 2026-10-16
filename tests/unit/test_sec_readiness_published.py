"""The readiness route against the index actually published in R2.

These are integration tests: they read the real objects under `gold/sec-readiness/index/` and the
record release they screen, and check the answers the terminal would get. The point is not that
the route runs, but that its answers agree with the record release it claims to summarise and that
its own screening filters do not contradict each other.

    python3 -m pytest tests/unit/test_sec_readiness_published.py -m integration
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in ("deploy/cloudflare-query/src", "scripts"):
    sys.path.insert(0, str(ROOT / extra))

import sec_readiness_api as route  # noqa: E402
from reconcile_financial_inventory import client as r2  # noqa: E402

BUCKET = "financial-system-datasets"
DEV_CONFIG = ROOT / "deploy/cloudflare-query/thin/wrangler.jsonc"
RECORD_SUMMARY = ("gold/sec-readiness/releases/"
                  "fc3233bd6673f1cbe9502216fd74e3351ef1fd2bfc8de596e6c98ecb388dc661/summary.json")

pytestmark = pytest.mark.integration


def pinned(env):
    """The readiness pin exactly as the deployment configuration states it, not as this file does."""
    text = DEV_CONFIG.read_text()
    values = {}
    for name in ("SEC_READINESS_INDEX_KEY", "SEC_READINESS_INDEX_SHA256"):
        match = re.search(r'"%s"\s*:\s*"([^"]+)"' % name, text)
        assert match, f"{DEV_CONFIG} does not pin {name}"
        values[name] = match.group(1)
    return SimpleNamespace(**values, ZION_PRIVATE_API_TOKEN=env["ZION_PRIVATE_API_TOKEN"],
                           MARKET_DATA=RealBucket(env["client"]))


class RealObject:
    def __init__(self, raw):
        self.raw, self.size = raw, len(raw)

    async def text(self):
        return self.raw.decode()


class RealBucket:
    """The two R2 operations the route uses, against the published bucket."""

    def __init__(self, client):
        self.client = client
        self.reads = []

    async def get(self, key):
        self.reads.append(key)
        try:
            body = self.client.get_object(Bucket=BUCKET, Key=key)
        except Exception:
            return None
        try:
            return RealObject(body["Body"].read())
        finally:
            body["Body"].close()


@pytest.fixture(scope="module")
def env():
    client = r2()
    token = "integration-test-token"
    return {"client": client, "ZION_PRIVATE_API_TOKEN": token}


def ask(env, params):
    request = SimpleNamespace(
        url="https://test/private/v1/readiness?" + urlencode(params), method="GET",
        headers={"authorization": "Bearer " + env["ZION_PRIVATE_API_TOKEN"]})
    return asyncio.run(route.handle(pinned(env), request))


def test_the_published_counts_agree_with_the_record_release_it_screens(env):
    result, status = ask(env, {"view": "summary"})
    assert status == 200, result
    body = env["client"].get_object(Bucket=BUCKET, Key=RECORD_SUMMARY)
    record_summary = json.loads(body["Body"].read())
    record_manifest = json.loads(env["client"].get_object(
        Bucket=BUCKET, Key=result["provenance"]["record_manifest"])["Body"].read())
    assert result["release_id"] == record_manifest["release_id"]
    assert result["issuers"] == record_summary["denominators"]["issuers_inspected"] == 20396
    assert result["counts"]["methods"] == {
        name: entry["eligible"] for name, entry in record_summary["method_eligibility"].items()}
    assert result["counts"]["flags"]["financial_history_available"] == 15136
    assert result["counts"]["status"] == {"CLASSIFIED": 20326, "SOURCE_IDENTITY_MISMATCH": 70}
    # One object, no archive bytes: an interactive screening question is not a universe scan.
    assert result["limits"] == {"index_objects_read": 1, "archive_bytes_read": 0}


@pytest.mark.parametrize("method,expected", [("operating_dcf", 2765), ("bank_excess_return", 698),
                                             ("fcfe", 2754), ("residual_income", 4748)])
def test_each_method_screen_matches_its_published_count(env, method, expected):
    first, status = ask(env, {"method": method, "limit": 5})
    assert status == 200, first
    assert first["total_matches"] == expected
    assert len(first["matches"]) == 5 and first["next_cursor"]
    assert all(method in row["methods"] for row in first["matches"])
    assert first["limits"]["filter_artifact_bytes"] <= route.MAX_FILTER_BYTES
    assert first["limits"]["archive_bytes_read"] == 0
    second, status = ask(env, {"method": method, "limit": 5, "cursor": first["next_cursor"]})
    assert status == 200
    assert {row["cik"] for row in first["matches"]}.isdisjoint(
        {row["cik"] for row in second["matches"]})


def test_a_verified_share_basis_and_a_missing_share_count_are_disjoint(env):
    """The screening filters cannot describe the same issuer as both supply and gap."""
    verified, status = ask(env, {"flag": "dated_share_basis_verified", "limit": 500})
    assert status == 200 and verified["total_matches"] == 6097
    missing, status = ask(env, {"missing": "MISSING_DATED_OUTSTANDING_SHARES", "limit": 500})
    assert status == 200 and missing["total_matches"] == 14229
    assert 6097 + 14229 <= 20396
    have = {row["cik"] for row in verified["matches"]}
    lack = {row["cik"] for row in missing["matches"]}
    assert not (have & lack)


def test_an_unclassified_issuer_has_no_valuation_verdict_and_no_invented_zero(env):
    result, status = ask(env, {"status": "SOURCE_IDENTITY_MISMATCH", "limit": 70})
    assert status == 200 and result["total_matches"] == 70
    assert all(row["methods"] == [] and row["missing"] == [] for row in result["matches"])
    assert all(row["status"] == "SOURCE_IDENTITY_MISMATCH" for row in result["matches"])


def test_a_named_issuer_is_answered_from_one_shard_with_its_evidence_pointer(env):
    result, status = ask(env, {"cik": "0000320193"})
    assert status == 200 and result["total_matches"] == 1
    row = result["matches"][0]
    assert row["cik"] == "0000320193" and row["label"].startswith("Apple")
    assert "operating_dcf" in row["methods"]
    assert re.fullmatch(r"part-\d{4}\.jsonl\.gz", row["part"])
    assert [ref["key"].split("/")[-1] for ref in result["provenance"]["index_artifacts"]] == \
        ["%02d.json" % (320193 % 100)]
    assert result["provenance"]["record_release_id"] == result["release_id"]


def test_the_companion_summary_object_is_the_published_one(env):
    """The summary a reader can fetch directly is the shape the route serves as its manifest view."""
    manifest, status = ask(env, {"view": "summary"})
    assert status == 200
    key = manifest["provenance"]["index_manifest"].replace("manifest.json", "summary.json")
    body = env["client"].get_object(Bucket=BUCKET, Key=key)
    published = json.loads(body["Body"].read())
    assert published["index_id"] == manifest["index_id"]
    assert published["counts"] == manifest["counts"]
    assert published["issuers"] == manifest["issuers"]
