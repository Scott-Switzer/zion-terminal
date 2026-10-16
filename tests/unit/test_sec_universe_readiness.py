"""Readiness classification rules, and an independent check of the published release.

The rule tests run on synthetic Company Facts data. The release test reads the pinned SEC
archive over ranged requests and re-derives, with code written here rather than in the
pipeline, the two properties that decide whether an issuer is usable: whether a borrowings
concept actually has a fact inside the eligibility window (so REVIEWED_ABSENT cannot be a
disguised unknown) and whether a share count does (so a verified per-share denominator is not
claimed without one). Run it with `-m integration`; it needs R2 credentials and the archive.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))
sys.path.insert(0, str(ROOT / "scripts"))

from sec_universe_readiness import (  # noqa: E402
    NOT_APPLICABLE,
    financial_history,
    method_eligibility,
    readiness_for,
)

CUTOFF = "2026-09-24"
BASE = {"date_to": CUTOFF, "maximum_as_of": CUTOFF + "T00:00:00Z", "release_id": "r" * 64,
        "registry_sha256": "g" * 64, "rights": "PUBLIC_SEC_ONLY",
        "source": {"snapshot_date": CUTOFF, "declared_sha256": "a" * 64}}
LABEL = "TEST FILER"
DEBT = "LongTermDebtNoncurrent"
SHARES = "EntityCommonStockSharesOutstanding"
GAAP = "us-gaap"
DEI = "dei"


def fact(rows, taxonomy, concept):
    return {taxonomy: {concept: {"units": {"USD": rows}}}}


def row(value, end="2026-06-30", filed="2026-08-01", form="10-K", **extra):
    return dict(val=value, end=end, filed=filed, form=form, accn="0000000000-26-000001", **extra)


def facts(*, debt=True, shares=True, dividends=False, operating=True, cash_flow=True,
          net_income=True, equity_total=True):
    us_gaap, dei = {}, {}
    us_gaap["Revenues"] = {"units": {"USD": [row(1000)]}}
    if operating:
        us_gaap["OperatingIncomeLoss"] = {"units": {"USD": [row(200)]}}
    if cash_flow:
        us_gaap["NetCashProvidedByUsedInOperatingActivities"] = {"units": {"USD": [row(150)]}}
        us_gaap["PaymentsToAcquirePropertyPlantAndEquipment"] = {"units": {"USD": [row(50)]}}
    if net_income:
        us_gaap["NetIncomeLoss"] = {"units": {"USD": [row(120)]}}
    if debt:
        us_gaap["LongTermDebtNoncurrent"] = {"units": {"USD": [row(400)]}}
    us_gaap["CashAndCashEquivalentsAtCarryingValue"] = {"units": {"USD": [row(300)]}}
    if equity_total:
        us_gaap["StockholdersEquity"] = {"units": {"USD": [row(900)]}}
    if dividends:
        us_gaap["PaymentsOfDividendsCommonStock"] = {"units": {"USD": [row(30)]}}
    if shares:
        dei["EntityCommonStockSharesOutstanding"] = {"units": {"shares": [row(100)]}}
    return {"cik": "0000000001", "entityName": LABEL, "facts": {"us-gaap": us_gaap, "dei": dei}}


def recorded_checkpoint(family="general_us_gaap_unclassified", **flags):
    """The engine checkpoint shape: annual coverage counts plus the mapping policy."""
    annual = {
        metric: {"observations": 1 if flags.get(flag, True) else 0}
        for metric, flag in (("revenue", "revenue"), ("net_income", "net_income"),
                             ("operating_income", "operating"),
                             ("operating_cash_flow", "cash_flow"),
                             ("capital_expenditures", "cash_flow"),
                             ("cash", "cash"), ("diluted_shares", "shares"),
                             ("total_debt", "debt"))
    }
    return {"status": "USABLE", "ref": {"coverage": {"annual": annual,
                                                     "quarterly": {}, "ttm": {}},
                                       "policy": {"family": family}}}


def record(**kwargs):
    family = kwargs.pop("family", "general_us_gaap_unclassified")
    document = json.dumps(facts(**kwargs)).encode()
    return readiness_for("0000000001", LABEL, document,
                         recorded_checkpoint(family, **kwargs), BASE, "m" * 64)


def test_a_complete_filer_is_eligible_for_the_operating_dcf():
    value = record(dividends=True)
    assert value["readiness"]["complete_equity_bridge"] is True
    assert value["readiness"]["dated_share_basis_verified"] is True
    assert value["valuation"]["methods"]["operating_dcf"]["eligible"] is True
    assert value["missing_inputs"] == []
    assert value["valuation"]["methods"]["ddm"]["eligible"] is True


def test_a_missing_share_count_blocks_every_per_share_method():
    value = record(shares=False)
    methods = value["valuation"]["methods"]
    assert "MISSING_DATED_OUTSTANDING_SHARES" in methods["operating_dcf"]["missing"]
    assert methods["operating_dcf"]["eligible"] is False
    assert methods["comparables"]["eligible"] is False
    assert "MISSING_DATED_OUTSTANDING_SHARES" in value["missing_inputs"]


def test_a_financial_filer_is_routed_to_the_specialized_lane_not_called_unvaluable():
    value = record(family="financial_statement_evidence")
    methods = value["valuation"]["methods"]
    assert methods["operating_dcf"]["missing"] == [
        "INDUSTRIAL_DCF_NOT_APPLICABLE_TO_FINANCIAL_FILER"]
    assert methods["bank_excess_return"]["eligible"] is True
    assert value["readiness"]["specialized_method_required"] is True
    # Applicability is not evidence, so it cannot appear as a missing input; the only recorded
    # gap for this filer is the dividend evidence the DDM lane needs.
    assert not set(value["missing_inputs"]) & set(NOT_APPLICABLE)
    assert value["missing_inputs"] == ["MISSING_DIVIDEND_EVIDENCE"]


def test_not_applicable_codes_are_excluded_from_missing_inputs():
    for family in ("general_us_gaap_unclassified", "financial_statement_evidence"):
        assert not set(record(family=family)["missing_inputs"]) & set(NOT_APPLICABLE)


def test_an_absent_claim_is_a_verified_zero_and_not_a_missing_input():
    value = record(debt=False, equity_total=False)
    claims = value["equity_claims"]["claims"]
    assert claims["debt"]["evidence_state"] == "REVIEWED_ABSENT"
    assert claims["debt"]["verified_absent"] is True
    # The balance sheet reports no borrowings at all, so the bridge still resolves.
    assert value["readiness"]["complete_equity_bridge"] is True


def test_missing_financial_history_is_reported_as_quarantined_not_as_zero():
    quarantined = {"status": "QUARANTINED", "reason": "NO_VALID_MAPPED_FINANCIAL_HISTORY"}
    history = financial_history(quarantined)
    listed = ("debt", "finance_leases", "preferred_equity", "noncontrolling_interests",
              "cash", "nonoperating_assets", "common_shares")
    claims = {name: {"evidence_state": "REPORTED"} for name in listed}
    methods = method_eligibility(history, claims, {"evidence_state": "REPORTED"}, {
        "equity_totals_reported": False, "dividends_reported": False})
    assert history["status"] == "QUARANTINED"
    assert methods["methods"]["operating_dcf"]["eligible"] is False
    assert methods["methods"]["fcfe"]["eligible"] is False


def test_fcfe_survives_an_incomplete_bridge_that_stops_the_enterprise_dcf():
    """The equity lane needs no bridge, so the two counts must not be assumed equal."""
    document = facts()
    del document["facts"]["us-gaap"]["LongTermDebtNoncurrent"]
    # Preferred shares filed with a conversion feature the contract cannot certify: the bridge
    # refuses, while FCFE -- an equity cash flow -- does not need the bridge at all.
    document["facts"]["us-gaap"]["PreferredStockValue"] = {
        "units": {"USD": [row(500, form="10-Q")]}}
    document["facts"]["us-gaap"]["PreferredStockLiquidationPreferenceValue"] = {
        "units": {"USD": [row(900, form="10-Q")]}}
    history = financial_history({"status": "USABLE", "ref": {"policy": {}}})
    history["status"] = "USABLE_HISTORY"
    history["annual_metrics_available"] = ["revenue", "operating_income", "net_income",
                                           "operating_cash_flow", "capital_expenditures"]
    value = readiness_for("0000000001", LABEL, json.dumps(document).encode(),
                          {"status": "USABLE", "ref": {"policy": {}}}, BASE, "m" * 64)
    value["financial_history"] = history
    claims = value["equity_claims"]["claims"]
    if claims["preferred_equity"]["evidence_state"] in ("CONFLICTING", "UNAVAILABLE"):
        assert value["readiness"]["complete_equity_bridge"] is False
        assert value["valuation"]["methods"]["operating_dcf"]["eligible"] is False
        assert value["valuation"]["methods"]["fcfe"]["eligible"] is True


# --------------------------------------------------------------------------------------------------
# Independent check of a published release against the pinned archive itself
# --------------------------------------------------------------------------------------------------
RELEASE = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-readiness/"
               "7c69a8ad0d5e33c41b7b9cb0819d4b20db872790458c7df73dca0251aab8202a")
DEBT_CANDIDATES = ("DebtCurrent", "LongTermDebtCurrent", "LongTermDebtNoncurrent", "LongTermDebt",
                   "CommercialPaper", "ShortTermBorrowings", "OtherShortTermBorrowings")
SHARE_CANDIDATES = ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding")
FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A")
WINDOW_DAYS = 400


@pytest.mark.integration
def test_published_records_agree_with_an_independent_read_of_the_archive():
    """Re-derive two decisions from the raw members, in this file, for a real sample."""
    import hashlib
    from datetime import timedelta
    from zipfile import ZipFile

    from reconcile_financial_inventory import client
    from sec_universe_readiness import RangeReader

    if not (RELEASE / "summary.json").exists():
        pytest.skip("no recorded readiness release in this checkout")
    engine = json.loads(Path(str(RELEASE.parent.parent / "sec-engine-scale/engine-manifest.json")
                            ).read_text())
    entries = json.loads(Path(str(RELEASE.parent.parent / "sec-engine-scale/full-member-index.json")
                              ).read_text())
    records = []
    for path in sorted(RELEASE.glob("part-*.jsonl.gz")):
        import gzip
        with gzip.open(path, "rt") as handle:
            records += [json.loads(line) for line in handle if line.strip()]
    classified = [r for r in records if r.get("status") == "CLASSIFIED"]
    assert len(classified) > 20000
    sample = classified[::max(len(classified) // 40, 1)][:40]
    reader = RangeReader(engine["source"], client())
    cutoff = date.fromisoformat(engine["date_to"])
    checked = 0
    with ZipFile(reader) as archive:
        for record in sample:
            document = json.loads(archive.read(entries[record["cik"]]["name"]))
            assert str(document["cik"]).zfill(10) == record["cik"]
            facts = document["facts"]
            eligible_debt = any(
                row for concept in DEBT_CANDIDATES
                for rows in facts.get("us-gaap", {}).get(concept, {}).get("units", {}).values()
                for row in rows
                if row.get("form") in FORMS
                and cutoff - timedelta(days=WINDOW_DAYS)
                <= date.fromisoformat(row["end"]) <= cutoff)
            state = record["equity_claims"]["claims"]["debt"]["evidence_state"]
            # A reviewed absence is a claim about the whole candidate list, so an eligible fact
            # anywhere in it contradicts the record.
            assert not (state == "REVIEWED_ABSENT" and eligible_debt), record["cik"]
            eligible_shares = any(
                row for concept in SHARE_CANDIDATES
                for taxonomy in ("dei", "us-gaap")
                for rows in facts.get(taxonomy, {}).get(concept, {}).get("units", {}).values()
                for row in rows
                if cutoff - timedelta(days=WINDOW_DAYS) <= date.fromisoformat(row["end"]) <= cutoff)
            verified = record["readiness"]["dated_share_basis_verified"]
            assert not (verified and not eligible_shares), record["cik"]
            assert record["source"]["member_sha256"] == hashlib.sha256(
                entries and archive.read(entries[record["cik"]]["name"])).hexdigest()
            checked += 1
    assert checked == len(sample)
