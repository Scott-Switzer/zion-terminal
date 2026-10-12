"""Contract tests for SEC annual fundamentals mapping and quality states."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from zion_terminal.datasets import sec_fundamentals as sf
from zion_terminal.datasets.cli import load_fixture_bridge

FIXTURE_TREE = Path(__file__).resolve().parents[1] / "fixtures" / "datasets" / "tree"
CORPUS_PATH = (
    "sec-filing-intelligence/corpus/v1/"
    + "a" * 64
    + "/raw/sec/companyfacts/cik=0000000001/companyfacts.json"
)
AS_OF = "2026-10-11"


@pytest.fixture(scope="module")
def issuer_a_payload() -> dict:
    import json

    bridge = load_fixture_bridge(FIXTURE_TREE)
    payload, _ = bridge.read_all("filings", CORPUS_PATH, max_bytes=4 * 1024 * 1024)
    return json.loads(payload)


def observations(payload: dict, *, cik: str = "0000000001") -> list[sf.SourceObservation]:
    source = sf.SourceRecord(store="filings", path=CORPUS_PATH, sha256="0" * 64)
    return sf.parse_companyfacts(payload, source=source)


def test_contract_covers_the_requested_observations():
    ids = {spec.field_id for spec in sf.CONTRACT_FIELDS}
    assert {
        "revenue",
        "operating_income_ebit",
        "net_income_common",
        "depreciation_amortization",
        "capital_expenditure",
        "cash_and_equivalents",
        "debt_current",
        "debt_noncurrent",
        "debt_total_reported",
        "debt_short_term_borrowings",
        "diluted_weighted_average_shares",
    } == ids
    assert sf.DERIVED_FIELD == "interest_bearing_debt_total"
    assert sf.SHORT_TERM_DERIVED_FIELD == "debt_short_term_total"


def test_parse_materializes_only_contract_concepts(issuer_a_payload):
    rows = observations(issuer_a_payload)
    assert rows
    assert {row.field_id for row in rows} <= set(sf.FIELD_IDS)
    assert all(row.source.sha256 == "0" * 64 for row in rows)
    # The disclosure-only issuer tag is parsed but never contract equivalent.
    depreciation = [row for row in rows if row.concept == "Depreciation"]
    assert depreciation and all(row.contract_equivalent is False for row in depreciation)
    assert all(row.contract_equivalent for row in rows if row.field_id == "revenue")


def test_availability_uses_the_documented_date_only_policy(issuer_a_payload):
    rows = observations(issuer_a_payload)
    revenue = next(
        row for row in rows if row.field_id == "revenue" and row.period_end == "2025-12-31"
    )
    assert revenue.availability_evidence == "DATE_ONLY"
    # filed 2026-02-10 -> following New York midnight expressed in UTC.
    assert revenue.available_at == "2026-02-11T05:00:00Z"


def test_annual_periods_exclude_a_stub_duration(issuer_a_payload):
    periods = sf.annual_periods(observations(issuer_a_payload), limit=3)
    assert periods == ["2025-12-31", "2024-12-31", "2023-12-31"]


def test_missing_observation_is_reported_without_inventing_a_value(issuer_a_payload):
    rows = observations(issuer_a_payload)
    result = sf.evaluate_company(rows, ticker="TSTA", cik="0000000001", as_of=AS_OF)
    field = result.fields["2023-12-31"]["capital_expenditure"]
    assert field.state == sf.STATE_MISSING
    assert field.value is None
    assert "NO_SOURCE_ROW_FOR_PERIOD" in field.reason_codes


def test_unit_mismatch_is_reported_with_the_observed_unit(issuer_a_payload):
    rows = observations(issuer_a_payload)
    result = sf.evaluate_company(rows, ticker="TSTA", cik="0000000001", as_of=AS_OF)
    field = result.fields["2024-12-31"]["capital_expenditure"]
    assert field.state == sf.STATE_UNIT_MISMATCH
    assert "OBSERVED_UNIT:EUR" in field.reason_codes
    assert field.unit == "USD"


def test_fiscal_period_mismatch_is_reported(issuer_a_payload):
    rows = observations(issuer_a_payload)
    result = sf.evaluate_company(rows, ticker="TSTA", cik="0000000001", as_of=AS_OF)
    field = result.fields["2025-12-31"]["diluted_weighted_average_shares"]
    assert field.state == sf.STATE_PERIOD_MISMATCH
    assert "SOURCE_ROW_NOT_ALIGNED_TO_ANNUAL_PERIOD" in field.reason_codes


def test_point_in_time_blocks_a_later_filing(issuer_a_payload):
    rows = observations(issuer_a_payload)
    restricted = sf.evaluate_company(rows, ticker="TSTA", cik="0000000001", as_of=AS_OF)
    assert (
        restricted.fields["2025-12-31"]["depreciation_amortization"].state
        == sf.STATE_NOT_PIT_ELIGIBLE
    )
    unrestricted = sf.evaluate_company(rows, ticker="TSTA", cik="0000000001", as_of=None)
    field = unrestricted.fields["2025-12-31"]["depreciation_amortization"]
    assert field.value == Decimal("72")
    assert field.observation.available_at.startswith("2026-10-21")


def test_conflicting_source_concepts_are_kept_not_averaged(issuer_a_payload):
    rows = observations(issuer_a_payload)
    result = sf.evaluate_company(rows, ticker="TSTA", cik="0000000001", as_of=AS_OF)
    field = result.fields["2025-12-31"]["revenue"]
    assert field.state == sf.STATE_CONFLICT
    assert "CONFLICTING_SOURCE_CONCEPTS" in field.reason_codes
    assert field.value == Decimal("1200")  # the preferred concept still wins
    conflicts = [item for item in field.derivations if item["kind"] == "concept_disagreement"]
    assert conflicts and conflicts[0]["value"] == "1188"


def test_superseded_filings_are_recorded_and_the_latest_wins(issuer_a_payload):
    rows = observations(issuer_a_payload)
    result = sf.evaluate_company(rows, ticker="TSTA", cik="0000000001", as_of=AS_OF)
    field = result.fields["2024-12-31"]["revenue"]
    assert field.value == Decimal("1100")
    assert "SUPERSEDED_FILINGS_IGNORED" in field.reason_codes
    superseded = [item for item in field.derivations if item["kind"] == "superseded_observation"]
    assert [item["value"] for item in superseded] == ["1150"]


def test_net_income_to_common_is_preferred_over_consolidated(issuer_a_payload):
    rows = observations(issuer_a_payload)
    result = sf.evaluate_company(rows, ticker="TSTA", cik="0000000001", as_of=AS_OF)
    field = result.fields["2025-12-31"]["net_income_common"]
    assert field.concept == "NetIncomeLossAvailableToCommonStockholdersBasic"
    assert field.value == Decimal("200")
    assert "NET_INCOME_TO_COMMON_UNADJUSTED" not in field.reason_codes


def test_consolidated_net_income_is_labelled_as_a_transformation():
    payload = {
        "cik": 9,
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 100,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 30,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
            }
        },
    }
    rows = sf.parse_companyfacts(
        payload, source=sf.SourceRecord(store="financial", path="p", sha256="1" * 64)
    )
    result = sf.evaluate_company(rows, ticker="TSTX", cik="9", as_of=None)
    field = result.fields["2024-12-31"]["net_income_common"]
    assert field.concept == "NetIncomeLoss"
    assert field.state == sf.STATE_SOURCE_UNVALIDATED
    assert "NET_INCOME_TO_COMMON_UNADJUSTED" in field.reason_codes


def test_interest_bearing_debt_derivation_and_reported_total_reconciliation(monkeypatch):
    # Synthetic amounts are tiny, so tighten the materiality floor that real filings
    # use for statement rounding.
    monkeypatch.setattr(sf, "TOTAL_RECONCILIATION_ABSOLUTE_SLACK", Decimal("10"))
    parts = {
        "debt_noncurrent": sf.FieldResult(
            "debt_noncurrent",
            "n",
            "2025-12-31",
            sf.STATE_SOURCE_UNVALIDATED,
            Decimal("430"),
            "USD",
            "LongTermDebtNoncurrent",
        ),
        "debt_current": sf.FieldResult(
            "debt_current",
            "c",
            "2025-12-31",
            sf.STATE_SOURCE_UNVALIDATED,
            Decimal("95"),
            "USD",
            "LongTermDebtCurrent",
        ),
        "debt_short_term_borrowings": sf.FieldResult(
            "debt_short_term_borrowings",
            "s",
            "2025-12-31",
            sf.STATE_SOURCE_UNVALIDATED,
            Decimal("20"),
            "USD",
            "CommercialPaper",
        ),
        "debt_total_reported": sf.FieldResult(
            "debt_total_reported",
            "t",
            "2025-12-31",
            sf.STATE_SOURCE_UNVALIDATED,
            Decimal("530"),
            "USD",
            "LongTermDebt",
        ),
    }
    derived = sf.derive_interest_bearing_debt(
        parts, period_end="2025-12-31", reported=parts["debt_total_reported"]
    )
    assert derived.value == Decimal("525")
    assert derived.state == sf.STATE_PASS_DERIVED
    assert "ROUNDING_DIFFERENCE_WITHIN_SLACK" in derived.reason_codes
    assert derived.derivations[-1]["delta"] == "5"

    short_term = sf.derive_short_term_debt(parts, period_end="2025-12-31")
    assert short_term.value == Decimal("115")

    parts["debt_total_reported"].value = Decimal("900")
    disagreed = sf.derive_interest_bearing_debt(
        parts, period_end="2025-12-31", reported=parts["debt_total_reported"]
    )
    assert disagreed.state == sf.STATE_CONFLICT
    assert "REPORTED_TOTAL_DISAGREES_WITH_DERIVED_SUM" in disagreed.reason_codes


def test_derivation_requires_every_part():
    parts = {
        "debt_noncurrent": sf.FieldResult("debt_noncurrent", "n", "2025-12-31", sf.STATE_MISSING),
        "debt_current": sf.FieldResult(
            "debt_current",
            "c",
            "2025-12-31",
            sf.STATE_SOURCE_UNVALIDATED,
            Decimal("95"),
            "USD",
            "LongTermDebtCurrent",
        ),
    }
    derived = sf.derive_interest_bearing_debt(parts, period_end="2025-12-31")
    assert derived.state == sf.STATE_MISSING
    assert derived.value is None
    assert "MISSING_PART:debt_noncurrent" in derived.reason_codes


def test_alternative_tags_are_disclosed_when_a_field_is_missing():
    payload = {
        "cik": 9,
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 100,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
                "Depreciation": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 12,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
            }
        },
    }
    rows = sf.parse_companyfacts(
        payload, source=sf.SourceRecord(store="financial", path="p", sha256="2" * 64)
    )
    result = sf.evaluate_company(rows, ticker="TSTX", cik="9", as_of=None)
    field = result.fields["2024-12-31"]["depreciation_amortization"]
    assert field.state == sf.STATE_MISSING
    assert "ALTERNATIVE_TAG_NOT_CONTRACT_EQUIVALENT" in field.reason_codes
    tags = {item["concept"] for item in field.derivations if item["kind"] == "alternative_tag"}
    assert tags == {"Depreciation"}


def _company_result() -> sf.CompanyContractResult:
    payload = {
        "cik": 9,
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 100,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 40,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
            }
        },
    }
    rows = sf.parse_companyfacts(
        payload, source=sf.SourceRecord(store="financial", path="p", sha256="3" * 64)
    )
    return sf.evaluate_company(rows, ticker="TSTX", cik="9", as_of=None)


def test_cross_validation_promotes_only_the_preferred_concept():
    result = _company_result()
    sf.cross_validate(
        result,
        [
            {
                "field_id": "revenue",
                "period_end": "2024-12-31",
                "value": 100,
                "unit": "USD",
                "source": "second source",
            },
            {
                "field_id": "operating_income_ebit",
                "period_end": "2024-12-31",
                "value": 40,
                "unit": "USD",
                "source": "second source",
            },
        ],
    )
    assert result.fields["2024-12-31"]["revenue"].state == sf.STATE_SOURCE_VALIDATED
    assert "SECOND_SOURCE_AGREES" in result.fields["2024-12-31"]["revenue"].reason_codes
    assert result.fields["2024-12-31"]["operating_income_ebit"].state == sf.STATE_SOURCE_VALIDATED


def test_cross_validation_states_cover_dissent_mismatch_and_basis():
    result = _company_result()
    findings = sf.cross_validate(
        result,
        [
            {
                "field_id": "revenue",
                "period_end": "2024-12-31",
                "value": 101,
                "unit": "USD",
                "source": "second source",
            },
            {
                "field_id": "operating_income_ebit",
                "period_end": "2024-12-31",
                "value": 40,
                "unit": "EUR",
                "source": "second source",
            },
            {
                "field_id": "capital_expenditure",
                "period_end": "2024-12-31",
                "value": 5,
                "unit": "USD",
                "source": "other basis",
                "comparable": False,
            },
            {
                "field_id": "revenue",
                "period_end": "2019-12-31",
                "value": 5,
                "unit": "USD",
                "source": "second source",
            },
        ],
    )
    states = [finding["state"] for finding in findings]
    assert states == ["CONFLICT", "UNIT_MISMATCH", "NOT_COMPARABLE", "PERIOD_NOT_EVALUATED"]
    assert result.fields["2024-12-31"]["revenue"].state == sf.STATE_CONFLICT
    assert "SECOND_SOURCE_DISAGREES" in result.fields["2024-12-31"]["revenue"].reason_codes


def test_secondary_equivalent_concept_is_promoted():
    payload = {
        "cik": 9,
        "facts": {
            "us-gaap": {
                # Only the fallback revenue concept is available, but it is an
                # equivalent measure, so agreement with a second source validates it.
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 100,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
            }
        },
    }
    rows = sf.parse_companyfacts(
        payload, source=sf.SourceRecord(store="financial", path="p", sha256="5" * 64)
    )
    result = sf.evaluate_company(rows, ticker="TSTX", cik="9", as_of=None)
    sf.cross_validate(
        result,
        [
            {
                "field_id": "revenue",
                "period_end": "2024-12-31",
                "value": 100,
                "unit": "USD",
                "source": "second source",
            }
        ],
    )
    field = result.fields["2024-12-31"]["revenue"]
    assert field.concept == "Revenues"
    assert field.state == sf.STATE_SOURCE_VALIDATED


def test_basis_changing_concept_agreement_does_not_validate_the_field():
    payload = {
        "cik": 9,
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 100,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 30,
                                "accn": "a",
                                "form": "10-K",
                                "filed": "2025-02-01",
                            }
                        ]
                    }
                },
            }
        },
    }
    rows = sf.parse_companyfacts(
        payload, source=sf.SourceRecord(store="financial", path="p", sha256="4" * 64)
    )
    result = sf.evaluate_company(rows, ticker="TSTX", cik="9", as_of=None)
    sf.cross_validate(
        result,
        [
            {
                "field_id": "net_income_common",
                "period_end": "2024-12-31",
                "value": 30,
                "unit": "USD",
                "source": "second source",
            }
        ],
    )
    field = result.fields["2024-12-31"]["net_income_common"]
    assert field.state == sf.STATE_SOURCE_UNVALIDATED
    assert "SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT" in field.reason_codes


def test_report_hash_is_stable_and_content_sensitive():
    body = {"a": 1, "b": [1, 2, 3]}
    assert sf.frame_report_hash(body) == sf.frame_report_hash(dict(reversed(list(body.items()))))
    assert sf.frame_report_hash(body) != sf.frame_report_hash({"a": 2, "b": [1, 2, 3]})
