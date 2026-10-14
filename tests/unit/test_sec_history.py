import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from decimal import Decimal

import pytest
from sec_history import REGISTRY, HistoryError, history, normalize_sources


def source(rows, tag="Revenues"):
    return {
        "cik": 320193,
        "entityName": "Owned fixture",
        "facts": {"us-gaap": {tag: {"units": {"USD": rows}}}},
    }


def fact(start="2020-01-01", end="2020-12-31", val=100, filed="2021-02-01", accn="a"):
    return dict(
        start=start, end=end, val=val, filed=filed, accn=accn, form="10-K", fy=2020, fp="FY"
    )


def query(data, **kw):
    return history(
        data,
        metrics=["revenue"],
        frequency="annual",
        date_from="2020-01-01",
        date_to="2021-12-31",
        as_of="2022-01-01T00:00:00Z",
        **kw,
    )


def test_revision_choice_filters_availability_before_ranking_and_preserves_exact_values():
    data = normalize_sources(
        source(
            [
                fact(val=Decimal("9007199254740993.0001")),
                fact(val=Decimal("9007199254740994.0002"), filed="2022-02-01", accn="b"),
            ]
        ),
        "0000320193",
        "a" * 64,
    )
    assert query(data)["observations"][0]["value_decimal"] == "9007199254740993.0001"
    assert query(data)["observations"][0]["source_observation_ids"]


def test_conflicting_same_filing_values_are_gap_not_arbitrary_tie_break():
    data = normalize_sources(source([fact(val=1), fact(val=2)]), "0000320193", "a" * 64)
    r = query(data)
    assert r["observations"] == []
    assert any(g["reason"] == "CONFLICTING_SOURCE_VALUES" for g in r["coverage_gaps"])


def test_null_is_not_zero_and_invalid_units_or_nonfinite_values_rejected():
    data = normalize_sources(
        source([fact(val=None), fact(val=Decimal("NaN"))]), "0000320193", "a" * 64
    )
    assert query(data)["observations"] == []
    assert data["rejections"]


def test_fiscal_quarter_differences_fcf_and_ttm_require_complete_contexts():
    rows = [
        fact("2020-02-02", "2020-05-02", 10),
        fact("2020-02-02", "2020-08-01", 25),
        fact("2020-02-02", "2020-10-31", 45),
        fact("2020-02-02", "2021-01-30", 70),
    ]
    s = source(rows)
    s["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"] = s["facts"]["us-gaap"].pop(
        "Revenues"
    )
    s["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"] = {
        "units": {"USD": [{**r, "val": r["val"] // 5} for r in rows]}
    }
    data = normalize_sources(s, "0000320193", "a" * 64)
    r = history(
        data,
        metrics=["operating_cash_flow", "free_cash_flow"],
        frequency="quarterly",
        date_from="2020-01-01",
        date_to="2021-12-31",
        as_of="2022-01-01T00:00:00Z",
    )
    assert [
        x["value_decimal"] for x in r["observations"] if x["metric"] == "operating_cash_flow"
    ] == ["10", "15", "20", "25"]
    t = history(
        data,
        metrics=["free_cash_flow"],
        frequency="ttm",
        date_from="2020-01-01",
        date_to="2021-12-31",
        as_of="2022-01-01T00:00:00Z",
    )
    assert t["observations"][0]["value_decimal"] == "56"
    assert len(t["observations"][0]["source_observation_ids"]) >= 4
    broken = normalize_sources(source(rows[:2]), "0000320193", "a" * 64)
    assert (
        history(
            broken,
            metrics=["revenue"],
            frequency="ttm",
            date_from="2020-01-01",
            date_to="2021-12-31",
            as_of="2022-01-01T00:00:00Z",
        )["observations"]
        == []
    )


def test_registry_rejects_invalid_metrics_and_never_sums_weighted_shares():
    data = normalize_sources(source([fact()]), "0000320193", "a" * 64)
    with pytest.raises(HistoryError):
        history(
            data,
            metrics=["liabilities_as_debt"],
            frequency="annual",
            date_from="2020-01-01",
            date_to="2021-01-01",
            as_of="2022-01-01T00:00:00Z",
        )
    assert "diluted_shares" in REGISTRY


def test_missing_source_field_is_not_fabricated_source_null():
    row = fact()
    del row["val"]
    data = normalize_sources(source([row]), "0000320193", "a" * 64)
    assert not data["observations"]
    assert data["rejections"]["SOURCE_VALUE_FIELD_MISSING"] == 1


def test_large_exact_derivation_and_nonadditive_ttm_fail_closed():
    from sec_history import calculate

    parts = []
    for n, value in enumerate(["9007199254740993.000000000000000001", "0.000000000000000001"]):
        parts.append(
            {
                "metric": "revenue",
                "concept": "us-gaap:Revenues",
                "period_start": "2020-01-01",
                "period_end": "2020-12-31",
                "unit": "USD",
                "currency": "USD",
                "value_decimal": value,
                "available_at": "2021-01-01T00:00:00Z",
                "source_observation_ids": [str(n)],
                "source_terms": [{"source_observation_id": str(n), "coefficient": 1}],
            }
        )
    assert (
        calculate(parts, "SUM", "revenue", "2020-01-01", "2020-12-31")["value_decimal"]
        == "9007199254740993.000000000000000002"
    )
    data = normalize_sources(source([fact()]), "0000320193", "a" * 64)
    r = history(
        data,
        metrics=["diluted_shares"],
        frequency="ttm",
        date_from="2020-01-01",
        date_to="2021-12-31",
        as_of="2022-01-01T00:00:00Z",
    )
    assert not r["observations"]
    assert any(x["reason"] == "TTM_NOT_DEFINED_FOR_NONADDITIVE_METRIC" for x in r["coverage_gaps"])


def test_duplicate_records_and_same_values_across_approved_aliases_are_deterministic():
    s = source([fact(), fact()])
    s["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"] = s["facts"][
        "us-gaap"
    ]["Revenues"]
    data = normalize_sources(s, "0000320193", "a" * 64)
    result = query(data)
    assert len(result["observations"]) == 1
    assert result["observations"][0]["concept"].endswith(
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    )


def test_costco_16_week_final_fiscal_quarter_can_form_exact_ttm():
    rows = [
        fact("2020-08-31", "2020-11-22", 10, filed="2022-01-01"),
        fact("2020-08-31", "2021-02-14", 20, filed="2022-01-01"),
        fact("2020-08-31", "2021-05-09", 30, filed="2022-01-01"),
        fact("2020-08-31", "2021-08-29", 50, filed="2022-01-01"),
    ]
    s = source(rows)
    s["cik"] = 909832
    data = normalize_sources(s, "0000909832", "a" * 64)
    r = history(
        data,
        metrics=["revenue"],
        frequency="ttm",
        date_from="2020-01-01",
        date_to="2022-12-31",
        as_of="2023-01-01T00:00:00Z",
    )
    assert len(r["observations"]) == 1
    assert r["observations"][0]["value_decimal"] == "50"


def test_negative_capex_is_not_silently_converted_and_missing_debt_not_zero():
    s = source([fact(val=-10)], tag="PaymentsToAcquirePropertyPlantAndEquipment")
    data = normalize_sources(s, "0000320193", "a" * 64)
    result = history(
        data,
        metrics=["capital_expenditures", "total_debt"],
        frequency="annual",
        date_from="2020-01-01",
        date_to="2021-12-31",
        as_of="2022-01-01T00:00:00Z",
    )
    assert result["observations"] == []
    assert any(
        g["reason"] == "NEGATIVE_VALUE_INVALID_FOR_METRIC_DEFINITION"
        for g in result["coverage_gaps"]
    )


def test_rejected_negative_cumulative_capex_cannot_feed_quarter_or_fcf():
    s = source(
        [fact("2020-01-01", "2020-03-31", -10), fact("2020-01-01", "2020-06-30", 20)],
        tag="PaymentsToAcquirePropertyPlantAndEquipment",
    )
    s["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"] = {
        "units": {"USD": [fact("2020-04-01", "2020-06-30", 100)]}
    }
    data = normalize_sources(s, "0000320193", "a" * 64)
    result = history(
        data,
        metrics=["capital_expenditures", "free_cash_flow"],
        frequency="quarterly",
        date_from="2020-01-01",
        date_to="2021-01-01",
        as_of="2022-01-01T00:00:00Z",
    )
    assert not result["observations"]


def test_later_invalid_revision_does_not_restore_an_older_valid_value():
    later = fact(val=Decimal("NaN"), filed="2021-06-01", accn="bad")
    data = normalize_sources(source([fact(val=100), later]), "0000320193", "a" * 64)
    result = query(data)
    assert not result["observations"]
    assert any(
        x["reason"] == "LATEST_SOURCE_CONTEXT_INVALID_NO_OLDER_FALLBACK"
        for x in result["coverage_gaps"]
    )


def test_committed_registry_pins_exact_normalizer_implementation():
    import hashlib
    import json

    root = Path(__file__).parents[2]
    registry = json.loads((root / "contracts/sec-financial-metric-registry-v1.json").read_text())
    assert (
        registry["implementation_sha256"]
        == hashlib.sha256(
            (root / "deploy/cloudflare-query/src/sec_history.py").read_bytes()
        ).hexdigest()
    )


def test_debt_current_with_capital_leases_requires_noncurrent_lease_component():
    s = source([fact()])
    s["cik"] = 1045810
    for tag, value in [("DebtCurrent", 10), ("LongTermDebtNoncurrent", 90)]:
        row = fact(val=value)
        del row["start"]
        s["facts"]["us-gaap"][tag] = {"units": {"USD": [row]}}
    data = normalize_sources(s, "0001045810", "a" * 64)
    args = dict(
        metrics=["total_debt"],
        frequency="annual",
        date_from="2020-01-01",
        date_to="2021-12-31",
        as_of="2022-01-01T00:00:00Z",
    )
    assert not history(data, **args)["observations"]
    row = fact(val=5)
    del row["start"]
    s["facts"]["us-gaap"]["CapitalLeaseObligationsNoncurrent"] = {"units": {"USD": [row]}}
    assert (
        history(normalize_sources(s, "0001045810", "a" * 64), **args)["observations"][0][
            "value_decimal"
        ]
        == "105"
    )
