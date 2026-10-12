"""Generate the synthetic baseline/candidate corpus for the regression comparator.

Each scenario is a small, fully synthetic registry report pair. The reports carry
no real issuer, licensed or private data: store paths, object hashes and
accessions are obviously fake. They exist so every severity class in
``zio_terminal.datasets.compare`` is exercised by a committed, reproducible
artifact rather than by a mock.

Regenerate with::

    python tests/fixtures/datasets/build_comparison_fixtures.py

The output is deterministic: re-running the generator on an unchanged tree
produces byte-identical files, so the canary workflow can assert that the
committed corpus matches the generator.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "comparisons"

CONTRACT = "zion-sec-fundamentals-contract-v1"
FIELDS = [
    "revenue",
    "operating_income_ebit",
    "net_income_common",
    "depreciation_amortization",
    "capital_expenditure",
    "diluted_weighted_average_shares",
    "cash_and_equivalents",
    "debt_current",
    "debt_short_term_borrowings",
    "debt_noncurrent",
    "debt_total_reported",
    "interest_bearing_debt_total",
    "debt_short_term_total",
]

TSTA_CIK = "0000000001"
TSTB_CIK = "0000000002"
TSTA_SHA = "1" * 64
TSTB_SHA = "2" * 64
TSTA_PATH = "fixture-store-a/gold/fixture-release/companyfacts.zip"
TSTB_PATH = "fixture-store-a/gold/fixture-release/companyfacts.zip"
PACKET_SHA = "3" * 64
PUBLISHED_SHA = "4" * 64


def _digest(body: dict[str, Any]) -> str:
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _field(
    field_id: str,
    period_end: str,
    *,
    state: str,
    value: int | str | None,
    unit: str,
    concept: str | None,
    accession: str | None = None,
    filed: str | None = None,
    available_at: str | None = None,
    source_sha: str = TSTA_SHA,
    path: str = TSTA_PATH,
    member: str | None = None,
    cross: tuple[dict[str, Any], ...] = (),
    reason_codes: tuple[str, ...] = (),
    basis: str = "fixture basis",
) -> dict[str, Any]:
    observation = None
    if state not in {"MISSING_OBSERVATION"} and accession is not None:
        observation = {
            "field_id": field_id,
            "concept": concept,
            "taxonomy": "us-gaap",
            "unit": unit,
            "value": None if value is None else str(value),
            "period_kind": "duration"
            if field_id
            in {
                "revenue",
                "operating_income_ebit",
                "net_income_common",
                "depreciation_amortization",
                "capital_expenditure",
                "diluted_weighted_average_shares",
            }
            else "instant",
            "period_start": None,
            "period_end": period_end,
            "duration_days": 365,
            "fiscal_year": int(period_end[:4]),
            "fiscal_period": "FY",
            "form": "10-K",
            "frame": None,
            "accession": accession,
            "filed": filed,
            "available_at": available_at,
            "availability_evidence": "DATE_ONLY",
            "contract_equivalent": True,
            "source": {
                "store": "fixture-store-a",
                "path": path,
                "sha256": source_sha,
                "etag": '"fixture-etag"',
                "bytes": 1024,
                "member": member,
                "release_id": "5" * 32,
            },
        }
    return {
        "field_id": field_id,
        "label": field_id.replace("_", " "),
        "period_end": period_end,
        "state": state,
        "value": None if value is None else str(value),
        "unit": unit,
        "concept": concept,
        "basis": basis,
        "reason_codes": list(reason_codes),
        "transformations": [],
        "observation": observation,
        "candidates": [],
        "derivations": [],
        "cross_sources": [dict(item) for item in cross],
    }


def _agreement(source: str, value: int | str, *, unit: str = "USD") -> dict[str, Any]:
    return {
        "field_id": None,
        "period_end": None,
        "secondary_source": source,
        "secondary_value": str(value),
        "secondary_unit": unit,
        "basis_note": None,
        "state": "AGREES",
    }


PACKET_SOURCE = "approved packet fixture-valuation-packet-v1 cutoff 2026-10-11"
PUBLISHED_SOURCE = "published SEC facts release as_of 2026-09-08"


def _period_fields(
    *,
    period: str,
    accession: str,
    filed: str,
    available_at: str,
    revenue: int,
    net_income: int,
    cash: int,
    debt_current: int,
    debt_noncurrent: int,
    source_sha: str,
    path: str,
    member: str | None,
    cross_source: str,
    validated: bool,
) -> dict[str, dict[str, Any]]:
    primary_cross = (_agreement(cross_source, revenue),)
    return {
        "revenue": _field(
            "revenue",
            period,
            state="SOURCE_VALIDATED" if validated else "SOURCE_UNVALIDATED",
            value=revenue,
            unit="USD",
            concept="RevenueFromContractWithCustomerExcludingAssessedTax",
            accession=accession,
            filed=filed,
            available_at=available_at,
            source_sha=source_sha,
            path=path,
            member=member,
            cross=primary_cross,
            reason_codes=("SECOND_SOURCE_AGREES",) if validated else (),
        ),
        "net_income_common": _field(
            "net_income_common",
            period,
            state="SOURCE_UNVALIDATED",
            value=net_income,
            unit="USD",
            concept="NetIncomeLossAvailableToCommonStockholdersBasic",
            accession=accession,
            filed=filed,
            available_at=available_at,
            source_sha=source_sha,
            path=path,
            member=member,
        ),
        "cash_and_equivalents": _field(
            "cash_and_equivalents",
            period,
            state="SOURCE_VALIDATED" if validated else "SOURCE_UNVALIDATED",
            value=cash,
            unit="USD",
            concept="CashAndCashEquivalentsAtCarryingValue",
            accession=accession,
            filed=filed,
            available_at=available_at,
            source_sha=source_sha,
            path=path,
            member=member,
            cross=primary_cross,
            reason_codes=("SECOND_SOURCE_AGREES",) if validated else (),
        ),
        "debt_current": _field(
            "debt_current",
            period,
            state="SOURCE_VALIDATED" if validated else "SOURCE_UNVALIDATED",
            value=debt_current,
            unit="USD",
            concept="LongTermDebtCurrent",
            accession=accession,
            filed=filed,
            available_at=available_at,
            source_sha=source_sha,
            path=path,
            member=member,
            cross=primary_cross,
            reason_codes=("SECOND_SOURCE_AGREES",) if validated else (),
        ),
        "debt_noncurrent": _field(
            "debt_noncurrent",
            period,
            state="SOURCE_UNVALIDATED",
            value=debt_noncurrent,
            unit="USD",
            concept="LongTermDebtNoncurrent",
            accession=accession,
            filed=filed,
            available_at=available_at,
            source_sha=source_sha,
            path=path,
            member=member,
        ),
        "interest_bearing_debt_total": _field(
            "interest_bearing_debt_total",
            period,
            state="PASS_DERIVED",
            value=debt_current + debt_noncurrent,
            unit="USD",
            concept="LongTermDebtNoncurrent + LongTermDebtCurrent",
            source_sha=source_sha,
            path=path,
            member=member,
            reason_codes=("DERIVED_FROM_VALIDATED_PARTS",),
        ),
        "capital_expenditure": _field(
            "capital_expenditure",
            period,
            state="MISSING_OBSERVATION",
            value=None,
            unit="USD",
            concept=None,
            reason_codes=("NO_CONTRACT_OBSERVATION",),
        ),
    }


def _company(
    *,
    ticker: str,
    cik: str,
    source_sha: str,
    path: str,
    member: str | None,
    cross_source: str,
    independent: bool,
    validated: bool,
) -> dict[str, Any]:
    fields = {
        "2025-12-31": _period_fields(
            period="2025-12-31",
            accession=f"000000000{ticker[-1]}-26-000001",
            filed="2026-01-30",
            available_at="2026-01-31T04:00:00Z",
            revenue=100_000_000_000,
            net_income=20_000_000_000,
            cash=40_000_000_000,
            debt_current=5_000_000_000,
            debt_noncurrent=30_000_000_000,
            source_sha=source_sha,
            path=path,
            member=member,
            cross_source=cross_source,
            validated=validated,
        ),
        "2024-12-31": _period_fields(
            period="2024-12-31",
            accession=f"000000000{ticker[-1]}-25-000001",
            filed="2025-01-30",
            available_at="2025-01-31T04:00:00Z",
            revenue=95_000_000_000,
            net_income=18_000_000_000,
            cash=38_000_000_000,
            debt_current=4_000_000_000,
            debt_noncurrent=28_000_000_000,
            source_sha=source_sha,
            path=path,
            member=member,
            cross_source=cross_source,
            validated=validated,
        ),
    }
    states: dict[str, int] = {}
    for period_fields in fields.values():
        for entry in period_fields.values():
            states[entry["state"]] = states.get(entry["state"], 0) + 1
    checks = [
        {
            "check": "published_source_evidence_sha256",
            "expected": PUBLISHED_SHA,
            "observed": source_sha,
            "state": "MATCH",
            "note": "fixture published evidence",
        }
    ]
    if independent:
        checks.append(
            {
                "check": "approved_packet_sec_source_cash",
                "expected": PACKET_SHA,
                "observed": source_sha,
                "state": "DIFFERENT_SOURCE_OBJECT",
                "note": "fixture approval built from a different source object",
            }
        )
    return {
        "ticker": ticker,
        "cik": cik,
        "overall": "SOURCE_PARTIALLY_VALIDATED" if validated else "SOURCE_UNVALIDATED",
        "state_counts": dict(sorted(states.items())),
        "identity_source": {"kind": "approved_valuation_packet", "cik": cik},
        "facts_origin": {"origin": "fixture_corpus"},
        "source_identity": checks,
        "contract": {
            "ticker": ticker,
            "cik": cik,
            "as_of": "2026-10-11",
            "contract": CONTRACT,
            "periods": ["2025-12-31", "2024-12-31"],
            "observations_examined": 64,
            "source": {
                "store": "fixture-store-a",
                "path": path,
                "sha256": source_sha,
                "etag": '"fixture-etag"',
                "bytes": 1024,
                "member": member,
            },
            "field_states": {
                period: {field_id: entry["state"] for field_id, entry in period_fields.items()}
                for period, period_fields in fields.items()
            },
            "missing_fields": {
                period: sorted(
                    field_id
                    for field_id, entry in period_fields.items()
                    if entry["state"] == "MISSING_OBSERVATION"
                )
                for period, period_fields in fields.items()
            },
            "fields": fields,
            "limitations": [],
        },
        "comparisons": [],
        "blocking_issues": [],
    }


def build_base() -> dict[str, Any]:
    """The approved synthetic baseline: TSTA and TSTB, two annual periods."""
    report: dict[str, Any] = {
        "schema_version": "zion-financial-registry-report-v1",
        "generated_at": "2026-10-12T00:00:00+00:00",
        "mode": "fixture",
        "as_of": "2026-10-11",
        "contract": CONTRACT,
        "fields": FIELDS,
        "sources": [
            {
                "family_id": "sec_facts_release",
                "release_id": "6" * 64,
                "schema_version": "zion-sec-facts-release-v1",
                "artifact_count": 12,
                "artifact_sample": {"sampled": 3, "complete": True, "missing": []},
                "findings": ["RELEASE_MANIFEST_HASH_VERIFIED"],
            },
            {
                "family_id": "serving_release_candidate",
                "release_id": "7" * 32,
                "artifact_sample": {"sampled": 3, "complete": True, "missing": []},
                "findings": ["RELEASE_POINTER_RESOLVED"],
            },
        ],
        "inventory": {"reads": [], "quality_states": {}},
        "limitations": ["fixture limitation: aggregate counts are not field evidence"],
        "companies": [
            _company(
                ticker="TSTA",
                cik=TSTA_CIK,
                source_sha=TSTA_SHA,
                path=TSTA_PATH,
                member=None,
                cross_source=PACKET_SOURCE,
                independent=True,
                validated=True,
            ),
            _company(
                ticker="TSTB",
                cik=TSTB_CIK,
                source_sha=TSTB_SHA,
                path=TSTB_PATH,
                member="CIK0000000002.json",
                cross_source=PUBLISHED_SOURCE,
                independent=True,
                validated=True,
            ),
        ],
    }
    report["report_sha256"] = _digest(
        {key: value for key, value in report.items() if key != "generated_at"}
    )
    return report


def _company_in(report: dict[str, Any], ticker: str) -> dict[str, Any]:
    for company in report["companies"]:
        if company["ticker"] == ticker:
            return company
    raise KeyError(ticker)


def _entry(report: dict[str, Any], ticker: str, period: str, field_id: str) -> dict[str, Any]:
    return _company_in(report, ticker)["contract"]["fields"][period][field_id]


def _refresh(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute the content address and per-company roll-ups after a mutation."""
    for company in report["companies"]:
        fields = company["contract"]["fields"]
        states: dict[str, int] = {}
        for period_fields in fields.values():
            for entry in period_fields.values():
                states[entry["state"]] = states.get(entry["state"], 0) + 1
        company["state_counts"] = dict(sorted(states.items()))
        company["contract"]["field_states"] = {
            period: {field_id: entry["state"] for field_id, entry in period_fields.items()}
            for period, period_fields in fields.items()
        }
        company["contract"]["missing_fields"] = {
            period: sorted(
                field_id
                for field_id, entry in period_fields.items()
                if entry["state"] == "MISSING_OBSERVATION"
            )
            for period, period_fields in fields.items()
        }
    report["report_sha256"] = _digest(
        {key: value for key, value in report.items() if key != "generated_at"}
    )
    return report


# -- scenarios --------------------------------------------------------------
def _unchanged(base: dict[str, Any]) -> tuple[dict, dict]:
    return base, copy.deepcopy(base)


def _new_period(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    candidate["as_of"] = "2027-02-15"
    for ticker, cik in (("TSTA", TSTA_CIK), ("TSTB", TSTB_CIK)):
        company = _company_in(candidate, ticker)
        period_fields = _period_fields(
            period="2026-12-31",
            accession=f"000000000{ticker[-1]}-27-000001",
            filed="2027-01-29",
            available_at="2027-01-30T04:00:00Z",
            revenue=108_000_000_000,
            net_income=22_000_000_000,
            cash=45_000_000_000,
            debt_current=5_500_000_000,
            debt_noncurrent=32_000_000_000,
            source_sha=TSTA_SHA if ticker == "TSTA" else TSTB_SHA,
            path=TSTA_PATH,
            member=None if ticker == "TSTA" else "CIK0000000002.json",
            cross_source=PACKET_SOURCE if ticker == "TSTA" else PUBLISHED_SOURCE,
            validated=True,
        )
        company["contract"]["fields"]["2026-12-31"] = period_fields
        company["contract"]["periods"] = ["2026-12-31", "2025-12-31", "2024-12-31"]
        company["cik"] = cik
    return base, _refresh(candidate)


def _restatement(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTA", "2025-12-31", "revenue")
    entry["value"] = "101500000000"
    entry["observation"]["value"] = "101500000000"
    entry["observation"]["accession"] = "0000000001-26-000002"
    entry["observation"]["filed"] = "2026-03-15"
    entry["observation"]["available_at"] = "2026-03-16T04:00:00Z"
    entry["observation"]["source"]["sha256"] = "9" * 64
    entry["observation"]["source"]["path"] = "fixture-store-a/gold/fixture-release/v2.zip"
    for cross in entry["cross_sources"]:
        cross["secondary_value"] = "101500000000"
    return base, _refresh(candidate)


def _field_lost(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    del _company_in(candidate, "TSTA")["contract"]["fields"]["2025-12-31"]["cash_and_equivalents"]
    return base, _refresh(candidate)


def _observation_lost(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTB", "2025-12-31", "revenue")
    entry["state"] = "MISSING_OBSERVATION"
    entry["value"] = None
    entry["concept"] = None
    entry["observation"] = None
    entry["cross_sources"] = []
    entry["reason_codes"] = ["NO_CONTRACT_OBSERVATION"]
    return base, _refresh(candidate)


def _debt_concept_demoted(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    for period in ("2025-12-31", "2024-12-31"):
        entry = _entry(candidate, "TSTA", period, "debt_current")
        entry["concept"] = "DebtCurrent"
        entry["observation"]["concept"] = "DebtCurrent"
    return base, _refresh(candidate)


def _prohibited_substitution(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTA", "2025-12-31", "net_income_common")
    entry["concept"] = "NetIncomeLoss"
    entry["observation"]["concept"] = "NetIncomeLoss"
    entry["reason_codes"] = ["NET_INCOME_TO_COMMON_UNADJUSTED"]
    return base, _refresh(candidate)


def _invalid_unit_conversion(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTA", "2025-12-31", "revenue")
    entry["value"] = "100000000000000"
    entry["observation"]["value"] = "100000000000000"
    return base, _refresh(candidate)


def _contract_unit_violation(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTB", "2025-12-31", "cash_and_equivalents")
    entry["unit"] = "shares"
    entry["observation"]["unit"] = "shares"
    return base, _refresh(candidate)


def _source_version_mismatch(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTA", "2025-12-31", "revenue")
    entry["value"] = "100200000000"
    entry["observation"]["value"] = "100200000000"
    entry["observation"]["source"]["sha256"] = "8" * 64
    entry["observation"]["source"]["etag"] = '"fixture-etag-v2"'
    return base, _refresh(candidate)


def _unexplained_value_change(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTA", "2025-12-31", "revenue")
    entry["value"] = "105000000000"
    entry["observation"]["value"] = "105000000000"
    return base, _refresh(candidate)


def _manifest_integrity_failure(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    candidate["sources"][0]["error"] = {
        "code": "MANIFEST_HASH_MISMATCH",
        "message": "the manifest digest does not match the pointer",
    }
    candidate["sources"][1]["artifact_sample"] = {
        "sampled": 3,
        "complete": False,
        "missing": ["fixture/missing-artifact.json"],
    }
    return base, _refresh(candidate)


def _validation_downgrade(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTA", "2025-12-31", "revenue")
    entry["state"] = "SOURCE_UNVALIDATED"
    entry["reason_codes"] = []
    return base, _refresh(candidate)


def _non_independent_validation(base: dict[str, Any]) -> tuple[dict, dict]:
    """TSTB was single-source in the baseline; the candidate claims validation."""
    baseline = copy.deepcopy(base)
    company = _company_in(baseline, "TSTB")
    company["source_identity"] = []
    company["overall"] = "SOURCE_UNVALIDATED"
    for period in ("2025-12-31", "2024-12-31"):
        for field_id in ("revenue", "cash_and_equivalents", "debt_current"):
            entry = _entry(baseline, "TSTB", period, field_id)
            entry["state"] = "SOURCE_UNVALIDATED"
            entry["reason_codes"] = []
            entry["cross_sources"] = []
    baseline = _refresh(baseline)

    candidate = copy.deepcopy(baseline)
    company = _company_in(candidate, "TSTB")
    company["source_identity"] = [
        {
            "check": "published_source_evidence_sha256",
            "expected": TSTB_SHA,
            "observed": TSTB_SHA,
            "state": "MATCH",
            "note": "fixture published evidence points at the object read here",
        }
    ]
    company["overall"] = "SOURCE_PARTIALLY_VALIDATED"
    for period in ("2025-12-31", "2024-12-31"):
        for field_id in ("revenue", "cash_and_equivalents", "debt_current"):
            entry = _entry(candidate, "TSTB", period, field_id)
            entry["state"] = "SOURCE_VALIDATED"
            entry["reason_codes"] = ["SECOND_SOURCE_AGREES"]
            entry["cross_sources"] = [_agreement(PUBLISHED_SOURCE, entry["value"])]
    return baseline, _refresh(candidate)


def _forward_availability(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    entry = _entry(candidate, "TSTA", "2025-12-31", "revenue")
    entry["observation"]["available_at"] = "2026-12-01T04:00:00Z"
    return base, _refresh(candidate)


def _issuer_identity_change(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    company = _company_in(candidate, "TSTA")
    company["cik"] = "0000000009"
    company["contract"]["cik"] = "0000000009"
    return base, _refresh(candidate)


def _ticker_collision(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    impostor = copy.deepcopy(_company_in(candidate, "TSTB"))
    impostor["cik"] = "0000000010"
    impostor["contract"]["cik"] = "0000000010"
    candidate["companies"].append(impostor)
    return base, _refresh(candidate)


def _blocked_company(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    company = _company_in(candidate, "TSTA")
    company["blocking_issues"] = ["SOURCE_FACTS_UNAVAILABLE"]
    company["overall"] = "BLOCKED"
    company["contract"]["fields"] = {}
    company["contract"]["periods"] = []
    return base, _refresh(candidate)


def _as_of_regression(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    candidate["as_of"] = "2026-09-30"
    return base, _refresh(candidate)


def _sanitized_pair(base: dict[str, Any]) -> tuple[dict, dict]:
    baseline = _sanitized(base)
    candidate = copy.deepcopy(baseline)
    for company in candidate["companies"]:
        if company["ticker"] != "TSTB":
            continue
        entry = company["fields"]["2025-12-31"]["revenue"]
        entry["state"] = "SOURCE_UNVALIDATED"
        entry["reason_codes"] = []
    candidate["report_sha256"] = _digest(
        {key: value for key, value in candidate.items() if key != "report_sha256"}
    )
    return baseline, candidate


def _sanitized(report: dict[str, Any]) -> dict[str, Any]:
    """Project a full report into the shareable summary shape."""
    return {
        "schema_version": report["schema_version"],
        "mode": report["mode"],
        "as_of": report["as_of"],
        "contract": report["contract"],
        "report_sha256": report["report_sha256"],
        "source_families": [
            {
                "family_id": item.get("family_id"),
                "state": "error" if item.get("error") else "resolved",
                "findings": item.get("findings") or [],
                "artifact_sample_complete": (item.get("artifact_sample") or {}).get("complete"),
            }
            for item in report["sources"]
        ],
        "serving_coverage": {"supported_fields": [], "unsupported_fields": [], "metric_count": 0},
        "companies": [
            {
                "ticker": company["ticker"],
                "cik": company["cik"],
                "overall": company["overall"],
                "state_counts": company["state_counts"],
                "source_identity_states": [
                    {"check": item["check"].split("_")[0], "state": item["state"]}
                    for item in company["source_identity"]
                ],
                "blocking_issues": company["blocking_issues"],
                "fields": {
                    period: {
                        field_id: {
                            "state": entry["state"],
                            "value": entry["value"],
                            "unit": entry["unit"],
                            "concept": entry["concept"],
                            "reason_codes": entry["reason_codes"],
                        }
                        for field_id, entry in period_fields.items()
                    }
                    for period, period_fields in company["contract"]["fields"].items()
                },
            }
            for company in report["companies"]
        ],
        "limitations": report["limitations"],
    }


def _leakage_attempt(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    company = _company_in(candidate, "TSTA")
    company["facts_origin"] = {
        "origin": "sec_companyfacts_corpus",
        "object": {
            "store": "research",
            "path": "sec/bulk/sec-nightly-20260924/companyfacts.zip",
            "sha256": "b" * 64,
            "etag": '"leaky-etag-27"',
            "member": "CIK0000000001.json",
        },
    }
    candidate["inventory"] = {
        "reads": [
            {
                "store": "research",
                "path": "sec/bulk/sec-nightly-20260924/companyfacts.zip",
                "sha256": "b" * 64,
                "quality": "HASH_VERIFIED",
            }
        ],
        "quality_states": {"HASH_VERIFIED": 1},
    }
    candidate["limitations"] = list(candidate["limitations"]) + [
        "api_key: should-never-be-published",
        "Bearer leaky-session-token",
        "s3://financial-data-wrds-private/restricted/quarterly.parquet",
        "financial-system-datasets/private/seed.json",
    ]
    for period_fields in company["contract"]["fields"].values():
        for entry in period_fields.values():
            observation = entry.get("observation")
            if observation and observation.get("source"):
                observation["source"]["path"] = "sec/bulk/sec-nightly-20260924/companyfacts.zip"
    return base, _refresh(candidate)


def _incomparable_pair(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    candidate["mode"] = "live"
    return base, _refresh(candidate)


def _contract_mismatch(base: dict[str, Any]) -> tuple[dict, dict]:
    candidate = copy.deepcopy(base)
    candidate["contract"] = "zion-sec-fundamentals-contract-v2"
    for company in candidate["companies"]:
        company["contract"]["contract"] = "zion-sec-fundamentals-contract-v2"
    return base, _refresh(candidate)


Scenario = tuple[Callable[[dict[str, Any]], tuple[dict, dict]], str]

SCENARIOS: dict[str, Scenario] = {
    "unchanged": (_unchanged, "identical reports must produce no findings at all"),
    "new_period": (
        _new_period,
        "a newly published annual period is INFO: legitimate new filings are not a regression",
    ),
    "restatement": (
        _restatement,
        "a value change carried by a later accession for the same period is a permitted "
        "restatement, not an unexplained change",
    ),
    "field_lost": (
        _field_lost,
        "a previously available field missing from the candidate reduces coverage (WARNING)",
    ),
    "observation_lost": (
        _observation_lost,
        "a usable observation becoming MISSING_OBSERVATION is a WARNING",
    ),
    "debt_concept_demoted": (
        _debt_concept_demoted,
        "moving to a lower-priority contract concept for the debt split is a WARNING",
    ),
    "prohibited_substitution": (
        _prohibited_substitution,
        "substituting consolidated net income for common income is a prohibited accounting "
        "substitution (FAIL)",
    ),
    "invalid_unit_conversion": (
        _invalid_unit_conversion,
        "a thousand-fold move with an unchanged unit is an unconverted scale (FAIL)",
    ),
    "contract_unit_violation": (
        _contract_unit_violation,
        "a unit outside the contract unit for the field is a FAIL",
    ),
    "source_version_mismatch": (
        _source_version_mismatch,
        "a value change under the same accession with a changed source object revision is an "
        "unresolved change (WARNING)",
    ),
    "unexplained_value_change": (
        _unexplained_value_change,
        "a material value change with no new filing and no source revision is a FAIL",
    ),
    "manifest_integrity_failure": (
        _manifest_integrity_failure,
        "a release that stops resolving and a manifest sample that disappears are FAILs",
    ),
    "validation_downgrade": (
        _validation_downgrade,
        "a validated field becoming unvalidated is a WARNING",
    ),
    "non_independent_validation": (
        _non_independent_validation,
        "validation asserted while every corroborating source is the same stored object is a FAIL",
    ),
    "forward_availability": (
        _forward_availability,
        "an observation claimed usable after the report's own as-of instant is a FAIL",
    ),
    "issuer_identity_change": (
        _issuer_identity_change,
        "one ticker resolving to a different issuer CIK is contradictory identity (FAIL)",
    ),
    "ticker_collision": (
        _ticker_collision,
        "two issuer identities resolving to one ticker is a FAIL",
    ),
    "blocked_company": (
        _blocked_company,
        "a company that loses its source facts and gains a blocker is a FAIL",
    ),
    "as_of_regression": (
        _as_of_regression,
        "a candidate whose as-of instant moved earlier can only shrink coverage (WARNING)",
    ),
    "sanitized_pair": (
        _sanitized_pair,
        "two sanitized summaries compare on the public fields and record the skipped checks",
    ),
    "leakage_attempt": (
        _leakage_attempt,
        "private paths, hashes, credentials and restricted store names must never reach the "
        "sanitized output",
    ),
    "incomparable_pair": (
        _incomparable_pair,
        "a fixture report and a live report are not comparable without an explicit override",
    ),
    "contract_mismatch": (
        _contract_mismatch,
        "two different contract schemas are not comparable without an explicit override",
    ),
}


def _dump(value: Any) -> str:
    """One line per file: the reports are data snapshots, not prose."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    index: dict[str, Any] = {}
    for name, (builder, description) in SCENARIOS.items():
        baseline, candidate = builder(build_base())
        scenario_dir = OUTPUT / name
        scenario_dir.mkdir()
        (scenario_dir / "baseline.json").write_text(_dump(baseline))
        (scenario_dir / "candidate.json").write_text(_dump(candidate))
        index[name] = description
    (OUTPUT / "index.json").write_text(json.dumps({"scenarios": index}, indent=2) + "\n")
    print(f"wrote {len(index)} scenarios to {OUTPUT}")


if __name__ == "__main__":
    main()
