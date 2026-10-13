import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from private_financials import (  # noqa: E402
    PrivateFinancialsError,
    annual_revenue_growth,
    normalize_observation,
)


def annual_rows():
    values = ["100", "110", "121", "133.1", "146.41", "161.051"]
    return [
        dict(
            entity_id="issuer",
            metric_id="revenue",
            value_decimal=v,
            unit="USD",
            accounting_basis="us-gaap:Revenues",
            period_type="annual",
            period_start=f"{2020 + i}-01-01",
            period_end=f"{2020 + i}-12-31",
            available_at=f"{2021 + i}-02-01",
            evidence_id=f"e{i}",
        )
        for i, v in enumerate(values)
    ]


def test_exact_growth_retains_inputs_and_does_not_substitute_cagr():
    rows = [normalize_observation(r, release_id="r1") for r in annual_rows()]
    result = annual_revenue_growth(rows)
    assert [r["percent"] for r in result["year_over_year"]] == ["10"] * 5
    assert result["cumulative_percent"] == "61.051"
    assert "cagr" not in result
    assert result["inputs"][0]["evidence_id"] == "e0"
    assert result["inputs"][0]["serving_release_id"] == "r1"


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda r: r.pop(2), "INCOMPLETE_HISTORY"),
        (lambda r: r.append(deepcopy(r[-1])), "AMBIGUOUS_PERIOD"),
        (lambda r: r[0].update(value_decimal="0"), "NON_POSITIVE_REVENUE"),
        (lambda r: r[0].update(value_decimal="-1"), "NON_POSITIVE_REVENUE"),
        (lambda r: r[0].update(value_decimal="NaN"), "INVALID_OBSERVATION"),
        (lambda r: r[0].update(unit="EUR"), "INCOMPARABLE_OBSERVATIONS"),
        (lambda r: r[0].pop("accounting_basis"), "COMPARABILITY_NOT_ESTABLISHED"),
        (lambda r: r[0].update(accounting_basis="different"), "INCOMPARABLE_OBSERVATIONS"),
        (lambda r: r[0].update(entity_id="other"), "INCOMPARABLE_OBSERVATIONS"),
        (lambda r: r[0].update(period_type="quarterly"), "INCOMPARABLE_OBSERVATIONS"),
    ],
)
def test_growth_fails_closed(mutation, code):
    rows = annual_rows()
    mutation(rows)
    with pytest.raises(PrivateFinancialsError) as e:
        annual_revenue_growth([normalize_observation(r, release_id="r1") for r in rows])
    assert e.value.code == code


def test_52_53_week_fiscal_years_are_comparable():
    ends = ["2019-12-28", "2021-01-02", "2022-01-01", "2022-12-31", "2023-12-30", "2024-12-28"]
    starts = ["2018-12-30", "2019-12-29", "2021-01-03", "2022-01-02", "2023-01-01", "2023-12-31"]
    rows = annual_rows()
    for row, start, end in zip(rows, starts, ends):
        row.update(period_start=start, period_end=end)
    assert (
        len(
            annual_revenue_growth([normalize_observation(r, release_id="r") for r in rows])[
                "year_over_year"
            ]
        )
        == 5
    )


def test_missing_middle_year_is_not_bridged_even_with_enough_rows():
    rows = annual_rows()
    rows[2].update(period_start="2018-01-01", period_end="2018-12-31")
    with pytest.raises(PrivateFinancialsError, match="INCOMPLETE_HISTORY"):
        annual_revenue_growth([normalize_observation(r, release_id="r") for r in rows])


def test_normalization_preserves_missing_metadata_and_rejects_float():
    row = annual_rows()[0]
    row.pop("accounting_basis")
    normalized = normalize_observation(row, release_id="r")
    assert normalized["accounting_basis"] is None
    assert normalized["validation_state"] == "SOURCE_UNVALIDATED"
    assert normalized["available_at"] == row["available_at"]
    row["value_decimal"] = 1.1
    with pytest.raises(PrivateFinancialsError):
        normalize_observation(row, release_id="r")


def test_published_sec_revenue_contract_is_an_explicit_comparison_basis():
    rows = annual_rows()
    for row in rows:
        row.pop("accounting_basis")
        row.update(
            source_id="SEC", derivation_type="reported", producer_release="verified-producer"
        )
    normalized = [normalize_observation(row, release_id="r") for row in rows]
    assert normalized[0]["accounting_basis"] is None
    assert normalized[0]["comparison_basis"] == "zion:consolidated-reported-revenue:v1"
    assert len(annual_revenue_growth(normalized)["year_over_year"]) == 5


def test_producer_batch_ids_do_not_change_the_canonical_metric_definition():
    rows = annual_rows()
    for i, row in enumerate(rows):
        row.pop("accounting_basis")
        row.update(
            source_id="SEC", derivation_type="reported", producer_release=f"producer-batch-{i}"
        )
    result = annual_revenue_growth([normalize_observation(row, release_id="r") for row in rows])
    assert len(result["year_over_year"]) == 5
    assert len({row["producer_release"] for row in result["inputs"]}) == 6
