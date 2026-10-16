"""Screening filters for the published readiness release, against an in-memory release."""

import gzip
import json
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from sec_readiness_query import latest_release, matching, records  # noqa: E402


def record(cik, *, methods=(), missing=(), bridge=True, shares=True, financial=False,
           conflicting=False, status="CLASSIFIED"):
    names = ("operating_dcf", "fcfe", "apv", "bank_excess_return", "residual_income", "ddm",
             "comparables")
    return {
        "cik": cik, "label": "FILER " + cik, "status": status,
        "valuation": {"methods": {name: {"eligible": name in methods, "missing": []}
                                  for name in names}},
        "missing_inputs": list(missing),
        "readiness": {
            "financial_history_available": True, "complete_equity_bridge": bridge,
            "dated_share_basis_verified": shares,
            "any_implemented_method_eligible": bool(methods) and not financial,
            "operating_dcf_eligible": "operating_dcf" in methods,
            "specialized_method_required": financial},
        "equity_claims": {"claims": {
            name: {"permitted_for_valuation": False if conflicting else None}
            for name in ("debt", "preferred_equity")}},
    }


class FakeBody:
    """The subset of a boto3 streaming body the query tool reads."""

    def __init__(self, raw):
        self.raw = raw

    def read(self):
        return self.raw

    def close(self):
        return None


def object_response(raw):
    return {"Body": FakeBody(raw)}


class FakeClient:
    def __init__(self, release, rows):
        self.release, self.rows = release, rows

    def list_objects_v2(self, **kwargs):
        prefix = "gold/sec-readiness/releases/" + self.release["release_id"] + "/"
        return {"CommonPrefixes": [{"Prefix": prefix}]}

    def get_object(self, Bucket=None, Key=None):
        if Key.endswith("manifest.json"):
            return object_response(json.dumps(self.release).encode())
        if Key.endswith("summary.json"):
            return object_response(json.dumps({"release_id": self.release["release_id"]}).encode())
        body = "\n".join(json.dumps(row) for row in self.rows) + "\n"
        return object_response(gzip.compress(body.encode(), mtime=0))


def release(rows):
    return {"release_id": "r" * 64, "issuers": len(rows), "archive_cutoff": "2026-09-24",
            "parts": [{"part": "part-0000.jsonl.gz", "issuers": len(rows)}]}


def client(rows):
    return FakeClient(release(rows), rows)


def args(**overrides):
    values = dict(cik=None, method=None, missing=None, any_method=False, financial=False,
                  conflicting=False, shares_absent=False, financial_history_available=False,
                  complete_equity_bridge=False, dated_share_basis_verified=False,
                  any_implemented_method_eligible=False, operating_dcf_eligible=False,
                  specialized_method_required=False)
    values.update(overrides)
    return Namespace(**values)


def test_filters_are_conjunctive_and_narrow_rather_than_widen():
    rows = [record("0000000001", methods=("operating_dcf", "ddm"), missing=()),
            record("0000000002", methods=("ddm",), missing=("MISSING_DATED_OUTSTANDING_SHARES",)),
            record("0000000003", methods=(), missing=("MISSING_DATED_OUTSTANDING_SHARES",))]
    one, two, three = rows
    assert matching(one, args(method="operating_dcf"))
    assert matching(two, args(method="ddm"))
    assert not matching(two, args(method="operating_dcf",
                                 missing="MISSING_DATED_OUTSTANDING_SHARES"))
    assert not matching(three, args(method="ddm"))
    assert matching(three, args(missing="MISSING_DATED_OUTSTANDING_SHARES"))
    assert matching(one, args(complete_equity_bridge=True, dated_share_basis_verified=True))


def test_a_requested_cik_is_returned_even_when_it_could_not_be_classified():
    """An identity mismatch is the answer to "why is this issuer missing", not a silent absence."""
    unclassified = record("0000000004", status="SOURCE_IDENTITY_MISMATCH")
    assert matching(unclassified, args(cik=["0000000004"]))
    assert not matching(unclassified, args(cik=["0000000005"]))
    assert not matching(unclassified, args(cik=["0000000004"], method="ddm"))


def test_financial_and_conflicting_filters_are_about_different_things():
    bank = record("0000000006", methods=("bank_excess_return",), financial=True)
    contested = record("0000000007", methods=("ddm",), conflicting=True)
    assert matching(bank, args(financial=True))
    assert not matching(bank, args(conflicting=True))
    assert matching(contested, args(conflicting=True, method="ddm"))
    assert not matching(contested, args(financial=True))


def test_the_newest_published_release_is_selected_by_its_manifest():
    older = release([record("0000000008")])
    newer = {"release_id": "s" * 64, "issuers": 40000, "archive_cutoff": "2026-10-15",
             "parts": [{"part": "part-0000.jsonl.gz", "issuers": 40000}]}
    client = FakeClient(newer, [record("0000000008")])
    client.releases = [older, newer]
    selected = latest_release(client)
    assert selected["release_id"] == newer["release_id"]
    rows = list(records(selected, client))
    assert rows[0]["cik"] == "0000000008"


def test_records_stream_parts_rather_than_holding_the_release():
    rows = [record("000000000%s" % index) for index in range(3)]
    client = FakeClient(release(rows), rows)
    seen = list(records(client.release, client))
    assert len(seen) == 3
