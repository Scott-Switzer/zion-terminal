"""The readiness route against an index built by the publisher itself.

The index this route serves is produced by ``scripts/sec_readiness_index.py``. These tests build a
small index with the publisher's own functions, put its bytes in a fake bucket, and then exercise
the route, so the published shape and the served shape are checked against each other rather than
against two hand-written fixtures.
"""

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in ("deploy/cloudflare-query/src", "scripts"):
    sys.path.insert(0, str(ROOT / extra))

import sec_readiness_index as publisher  # noqa: E402

route = importlib.import_module("sec_readiness_api")
PREFIX = "gold/sec-readiness/index/"
RELEASE = "fc3233bd6673f1cbe9502216fd74e3351ef1fd2bfc8de596e6c98ecb388dc661"


def record(cik, label, status="CLASSIFIED", methods=(), missing=(), flags=(), shares="DEI",
           refused=(), absent=()):
    return {
        "cik": cik, "label": label, "status": status, "part": "part-0000.jsonl.gz",
        "missing_inputs": list(missing),
        "readiness": {name: name in flags or ("operating_dcf_eligible" in flags
                                              and name == "operating_dcf_eligible")
                      for name in publisher.FLAGS},
        "valuation": {"methods": {name: {"eligible": name in methods} for name in publisher.METHODS}},
        "equity_claims": {
            "shares": {"basis": shares, "evidence_state": "REPORTED"},
            "claims": {name: {"permitted_for_valuation": False if name in refused else None,
                              "verified_absent": name in absent}
                       for name in ("debt", "finance_leases", "preferred_equity",
                                    "noncontrolling_interests")},
        },
    }


RECORDS = [
    record("0000320193", "APPLE INC.", methods=("operating_dcf", "ddm", "comparables"),
           flags=("financial_history_available", "complete_equity_bridge",
                  "dated_share_basis_verified", "any_implemented_method_eligible",
                  "operating_dcf_eligible")),
    record("0001018724", "AMAZON COM INC", methods=("comparables",),
           missing=("MISSING_DATED_OUTSTANDING_SHARES", "MISSING_DIVIDEND_EVIDENCE"),
           flags=("any_implemented_method_eligible",), absent=("finance_leases",)),
    record("0001393612", "BANK HOLDING CO", methods=("bank_excess_return",),
           flags=("financial_history_available", "complete_equity_bridge",
                  "specialized_method_required"), shares=None),
    record("0000001750", "AAR CORP.", missing=("MISSING_DATED_OUTSTANDING_SHARES",),
           refused=("debt",)),
    record("0000001751", "IDENTITY MISMATCH CO", status="SOURCE_IDENTITY_MISMATCH"),
]


class FakeObject:
    def __init__(self, raw):
        self.raw, self.size = raw, len(raw)

    async def text(self):
        return self.raw.decode()


class FakeBucket:
    """Only the two R2 operations this route is allowed to use."""

    def __init__(self, objects):
        self.objects, self.reads = objects, []
        self.writes = []

    async def get(self, key):
        self.reads.append(key)
        raw = self.objects.get(key)
        return FakeObject(raw) if raw is not None else None


@pytest.fixture
def index(monkeypatch):
    monkeypatch.setattr(publisher, "records",
                        lambda release, client=None, local=None: iter(RECORDS))
    release = {"release_id": RELEASE, "issuers": len(RECORDS), "archive_cutoff": "2026-09-24",
               "parts": [{"part": "part-0000.jsonl.gz"}]}
    artifacts, rows, counts = publisher.build(release)
    manifest = publisher.manifest_for(
        release, artifacts, rows, counts,
        {"key": publisher.RECORD_PREFIX + RELEASE + "/manifest.json", "sha256": "r" * 64,
         "bytes": 10, "parts": []})
    objects = {PREFIX + manifest["index_id"] + "/" + name: entry["body"]
               for name, entry in artifacts.items()}
    objects[PREFIX + manifest["index_id"] + "/manifest.json"] = \
        publisher.encoded(manifest) + b"\n"
    objects[PREFIX + manifest["index_id"] + "/summary.json"] = \
        publisher.encoded(publisher.summary_for(manifest)) + b"\n"
    bucket = FakeBucket(objects)
    env = SimpleNamespace(
        MARKET_DATA=bucket, ZION_PRIVATE_API_TOKEN="test-secret",
        SEC_READINESS_INDEX_KEY=PREFIX + manifest["index_id"] + "/manifest.json",
        SEC_READINESS_INDEX_SHA256=publisher.digest(publisher.encoded(manifest) + b"\n"),
    )

    def query(params=None, token="test-secret", method="GET"):
        request = SimpleNamespace(
            url="https://test/private/v1/readiness?" + urlencode(params or {}),
            method=method, headers={"authorization": "Bearer " + token})
        return asyncio.run(route.handle(env, request))

    return SimpleNamespace(manifest=manifest, artifacts=artifacts, bucket=bucket, env=env,
                           query=query, counts=counts)


def test_summary_reads_the_manifest_only_and_reports_every_flag(index):
    result, status = index.query({"view": "summary"})
    assert status == 200
    assert result["release_id"] == RELEASE
    assert result["issuers"] == len(RECORDS)
    assert result["counts"]["status"] == {"CLASSIFIED": 4, "SOURCE_IDENTITY_MISMATCH": 1}
    assert result["counts"]["methods"]["operating_dcf"] == 1
    # A financial filer is routed to its own lane, not counted as an unvaluable general filer.
    assert result["counts"]["flags"]["specialized_method_required"] == 1
    assert index.bucket.reads == [index.env.SEC_READINESS_INDEX_KEY]
    assert result["limits"]["archive_bytes_read"] == 0


def test_screen_reads_one_filter_artifact_and_applies_the_rest_in_memory(index):
    result, status = index.query({"method": "operating_dcf",
                                  "flag": "dated_share_basis_verified", "limit": "1"})
    assert status == 200
    assert [row["cik"] for row in result["matches"]] == ["0000320193"]
    assert result["total_matches"] == 1
    assert result["next_cursor"] is None
    # One artifact is read, and it is the narrowest filter of the two: one issuer, not four.
    assert index.bucket.reads[-1].endswith("buckets/flag-dated_share_basis_verified.json")
    assert result["limits"]["filter_artifact_bytes"] < 500
    index.bucket.reads.clear()
    narrow, status = index.query({"method": "bank_excess_return",
                                  "flag": "financial_history_available"})
    assert status == 200 and narrow["total_matches"] == 1
    assert index.bucket.reads[-1].endswith("buckets/method-bank_excess_return.json")
    assert "flag" in narrow["filters"] and narrow["filters"]["flag"] == ["financial_history_available"]


def test_a_missing_input_filter_never_returns_an_eligible_issuer_and_never_a_zero(index):
    result, status = index.query({"missing": "MISSING_DATED_OUTSTANDING_SHARES",
                                  "method": "operating_dcf"})
    assert status == 200
    assert result["total_matches"] == 0 and result["matches"] == []
    result, status = index.query({"missing": "MISSING_DIVIDEND_EVIDENCE"})
    assert status == 200
    assert [row["cik"] for row in result["matches"]] == ["0001018724"]
    # A reviewed absence is a verified zero in the record; here it stays visible as such.
    assert result["matches"][0]["reviewed_absent_claims"] == 1


def test_pagination_is_bound_to_this_query_and_this_index(index):
    page, status = index.query({"flag": "any_implemented_method_eligible", "limit": "1"})
    assert status == 200 and page["total_matches"] == 2 and page["next_cursor"]
    second, status = index.query({"flag": "any_implemented_method_eligible", "limit": "1",
                                  "cursor": page["next_cursor"]})
    assert status == 200 and len(second["matches"]) == 1
    assert second["matches"][0]["cik"] != page["matches"][0]["cik"]
    mismatch, status = index.query({"flag": "dated_share_basis_verified", "limit": "1",
                                    "cursor": page["next_cursor"]})
    assert status == 409 and mismatch["error"]["code"] == "CURSOR_QUERY_OR_RELEASE_MISMATCH"
    other, status = index.query({"flag": "any_implemented_method_eligible", "limit": "1",
                                 "expected_index_id": "f" * 64})
    assert status == 409 and other["error"]["code"] == "SOURCE_VERSION_CHANGED"


def test_one_named_issuer_costs_one_shard_and_an_unknown_one_is_404(index):
    result, status = index.query({"cik": "0000320193"})
    assert status == 200
    assert [row["cik"] for row in result["matches"]] == ["0000320193"]
    assert result["matches"][0]["part"] == "part-0000.jsonl.gz"
    reads = [key for key in index.bucket.reads if "/cik/" in key]
    assert len(reads) == 1 and reads[0].endswith("cik/%02d.json" % (320193 % 100))
    missing, status = index.query({"cik": "0000001999"})
    assert status == 404 and missing["error"]["code"] == "SCREEN_CIK_NOT_MATERIALIZED"
    assert index.bucket.reads.count(index.env.SEC_READINESS_INDEX_KEY) == 2


def test_unclassified_issuers_stay_in_the_universe_and_are_not_screened_as_valuable(index):
    result, status = index.query({"status": "SOURCE_IDENTITY_MISMATCH"})
    assert status == 200 and result["total_matches"] == 1
    assert result["matches"][0]["methods"] == []
    summary, _ = index.query({"view": "summary"})
    assert sum(entry["issuers"] for name, entry in summary["filters"].items()
               if name.startswith("status-")) == len(RECORDS)
    assert "status-SOURCE_IDENTITY_MISMATCH" in summary["filters"]


def test_a_refused_claim_is_visible_rather_than_averaged_away(index):
    result, status = index.query({"cik": "0000001750"})
    assert status == 200 and result["matches"][0]["refused_claims"] == ["debt"]
    assert result["matches"][0]["methods"] == []
    assert "MISSING_DATED_OUTSTANDING_SHARES" in result["matches"][0]["missing"]


def test_source_identity_bounds_auth_and_budgets(index, monkeypatch):
    assert index.query({"method": "operating_dcf"}, token="wrong")[1] == 401
    assert index.query({"method": "operating_dcf"}, method="POST")[1] == 405
    assert index.query({"recipient": "someone_else"})[1] == 403
    assert index.query({"method": "not_a_method"})[1] == 422
    assert index.query({"missing": "NOT_A_PUBLISHED_CODE"})[1] == 404
    assert index.query({"view": "screen"})[1] == 422
    assert index.query({"flag": "flag", "limit": "0"})[1] == 422
    # A tampered manifest is refused before anything it declares is trusted.
    tampered = SimpleNamespace(**vars(index.env))
    tampered.SEC_READINESS_INDEX_SHA256 = "0" * 64
    request = SimpleNamespace(url="https://test/private/v1/readiness", method="GET",
                              headers={"authorization": "Bearer test-secret"})
    assert asyncio.run(route.handle(tampered, request))[1] == 409
    # A declared artifact that no longer hashes to its manifest entry is refused too.
    key = index.bucket.objects and [k for k in index.bucket.objects if k.endswith("buckets/flag-complete_equity_bridge.json")][0]
    original = index.bucket.objects[key]
    index.bucket.objects[key] = original.replace(b"APPLE INC.", b"APPLE INC")
    result, status = index.query({"flag": "complete_equity_bridge"})
    assert status == 409 and result["error"]["code"] == "SOURCE_VERSION_CHANGED"
    index.bucket.objects[key] = original


def test_the_declared_bytes_of_a_filter_bound_what_is_read(index, monkeypatch):
    monkeypatch.setattr(route, "MAX_FILTER_BYTES", 10)
    result, status = index.query({"method": "operating_dcf"})
    assert status == 409 and result["error"]["code"] == "SCREEN_SOURCE_SIZE_INVALID"
    monkeypatch.setattr(route, "MAX_FILTER_BYTES", 12 * 1048576)
    monkeypatch.setattr(route, "MAX_RESPONSE", 200)
    result, status = index.query({"method": "operating_dcf"})
    assert status == 413 and result["error"]["code"] == "SCREEN_RESPONSE_BUDGET_EXCEEDED"


def test_the_same_release_always_produces_the_same_index_id(index):
    artifacts = dict(index.artifacts)
    release = {"release_id": RELEASE, "issuers": len(RECORDS), "archive_cutoff": "2026-09-24",
               "parts": [{"part": "part-0000.jsonl.gz"}]}
    reference = {"key": publisher.RECORD_PREFIX + RELEASE + "/manifest.json",
                 "sha256": "r" * 64, "bytes": 10, "parts": []}
    again = publisher.manifest_for(release, artifacts, len(RECORDS),
                                   index.counts, reference)
    assert again["index_id"] == index.manifest["index_id"]
    assert json.loads(json.dumps(again)) == again
    moved = {name: dict(entry) for name, entry in artifacts.items()}
    moved["buckets/method-operating_dcf.json"] = dict(
        moved["buckets/method-operating_dcf.json"], sha256="a" * 64)
    other = publisher.manifest_for(release, moved, len(RECORDS), index.counts, reference)
    assert other["index_id"] != index.manifest["index_id"]


def test_union_within_method_filter_preserves_all_matches_and_deduplicates(index):
    result, status = index.query({'method': 'operating_dcf,comparables'})
    assert status == 200
    assert {row['cik'] for row in result['matches']} == {'0000320193', '0001018724'}
    assert result['total_matches'] == 2


def test_union_within_status_filter_preserves_all_groups(index):
    result, status = index.query({'status': 'CLASSIFIED,SOURCE_IDENTITY_MISMATCH'})
    assert status == 200
    assert result['total_matches'] == 5


@pytest.mark.parametrize('kind', ['sector', 'share_absence'])
def test_multi_group_candidate_selection_uses_the_whole_union(kind):
    artifacts = {'buckets/' + kind + '-' + name + '.json': {'issuers': 1}
                 for name in ('first', 'second')}
    filters = [[], [], [], [], [], []]
    filters[4 if kind == 'sector' else 5] = ['first', 'second']
    assert len(route.narrowest({'artifacts': artifacts, 'issuers': 2}, *filters)) == 2
