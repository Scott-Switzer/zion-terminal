import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/cloudflare-query/src"))


def checker():
    return importlib.import_module("sec_balance_quality")


def fact(
    concept,
    value,
    accession="0000909832-15-000014",
    available="2015-10-15T04:00:00Z",
    end="2014-08-31",
    unit="USD",
    currency="USD",
    form="10-K",
):
    return {
        "metric": "balance_component:" + concept,
        "concept": "us-gaap:" + concept,
        "value_decimal": value,
        "period_start": None,
        "period_end": end,
        "unit": unit,
        "currency": currency,
        "accession": accession,
        "form": form,
        "available_at": available,
        "source_observation_id": concept + accession,
        "source_record": {"end": end, "filed": available[:10]},
    }


def check(rows):
    return checker().balance_checks(
        {"observations": rows}, "2026-10-14T00:00:00Z", "2010-01-01", "2026-10-14"
    )[0]


def test_real_costco_five_historical_mixed_vintage_regression():
    # Exact selected source contexts from local public SEC diagnostics. No fixture network.
    examples = [
        (
            "2014-08-31",
            "32662000000",
            "33024000000",
            "16-000032",
            "2016-10-13T04:00:00Z",
            "15-000014",
            "2015-10-15T04:00:00Z",
            "10-K",
        ),
        (
            "2014-11-23",
            "34605000000",
            "34613000000",
            "15-000017",
            "2015-12-19T05:00:00Z",
            "14-000026",
            "2014-12-19T05:00:00Z",
            "10-Q",
        ),
        (
            "2015-02-15",
            "33217000000",
            "33600000000",
            "16-000023",
            "2016-03-10T05:00:00Z",
            "15-000003",
            "2015-03-12T04:00:00Z",
            "10-Q",
        ),
        (
            "2015-05-10",
            "33109000000",
            "33491000000",
            "16-000028",
            "2016-06-02T04:00:00Z",
            "15-000008",
            "2015-06-05T04:00:00Z",
            "10-Q",
        ),
        (
            "2015-11-22",
            "35037000000",
            "35451000000",
            "16-000040",
            "2016-12-17T05:00:00Z",
            "15-000017",
            "2015-12-19T05:00:00Z",
            "10-Q",
        ),
    ]
    legacy = importlib.import_module("sec_engine")
    for end, assets, total, aacc, aat, lacc, lat, form in examples:
        rows = [
            fact("Assets", assets, "0000909832-" + aacc, aat, end, form=form),
            fact(
                "LiabilitiesAndStockholdersEquity", total, "0000909832-" + lacc, lat, end, form=form
            ),
        ]
        assert (
            legacy.balance_checks({"observations": rows}, "2026-10-14T00:00:00Z", end, end)[0][
                "status"
            ]
            == "FAIL"
        )
        result = check(rows)
        assert result["status"] == "UNAVAILABLE"
        assert result["reason"] == "LATEST_BALANCE_TOTALS_DIFFERENT_FILING_VINTAGES"
        assert "difference_decimal" not in result
        assert result["available_at"] == max(aat, lat)
        assert set(result["source_observation_ids"]) == {r["source_observation_id"] for r in rows}
        assert result["balance_quality_version"] == checker().VERSION


def test_same_vintage_true_failure_and_pass_preserved():
    for assets, total, expected in [("2", "1", "FAIL"), ("2", "2", "PASS")]:
        result = check([fact("Assets", assets), fact("LiabilitiesAndStockholdersEquity", total)])
        assert result["status"] == expected
        assert result["difference_decimal"] == ("1" if expected == "FAIL" else "0")


def test_equal_numbers_different_vintage_still_unavailable_and_no_older_fill():
    older = fact("Assets", "2")
    latest = fact("Assets", "3", "0000909832-16-000032", "2016-10-13T04:00:00Z")
    total = fact("LiabilitiesAndStockholdersEquity", "3")
    result = check([older, latest, total])
    assert result["status"] == "UNAVAILABLE" and "difference_decimal" not in result
    assert older["source_observation_id"] not in result["source_observation_ids"]


def test_unit_currency_or_metadata_mismatch_unavailable():
    left = fact("Assets", "2")
    for right in [
        fact("LiabilitiesAndStockholdersEquity", "2", unit="EUR", currency="EUR"),
        fact("LiabilitiesAndStockholdersEquity", "2", form="10-K/A"),
        {**fact("LiabilitiesAndStockholdersEquity", "2"), "accession": None},
    ]:
        result = check([left, right])
        assert result["status"] == "UNAVAILABLE" and "difference_decimal" not in result
        assert len(result["source_observation_ids"]) == 2


def test_missing_fact_preserves_legacy_unavailable():
    result = check([fact("Assets", "2")])
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "EXACT_SCOPE_BALANCE_TOTAL_MISSING_OR_AMBIGUOUS"
    assert result["source_observation_ids"] == []


def test_unit_exception_isolated_and_same_availability_required():
    rows = [
        fact("Assets", "2"),
        fact("LiabilitiesAndStockholdersEquity", "2", unit="EUR", currency="EUR"),
        fact("Assets", "7", end="2015-08-30"),
        fact("LiabilitiesAndStockholdersEquity", "6", end="2015-08-30"),
    ]
    results = checker().balance_checks(
        {"observations": rows}, "2026-10-14T00:00:00Z", "2010-01-01", "2026-10-14"
    )
    assert [row["status"] for row in results] == ["UNAVAILABLE", "FAIL"]
    assert results[1]["difference_decimal"] == "1"
    changed_availability = fact(
        "LiabilitiesAndStockholdersEquity", "2", available="2015-10-16T04:00:00Z"
    )
    result = check([fact("Assets", "2"), changed_availability])
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "LATEST_BALANCE_TOTALS_DIFFERENT_FILING_VINTAGES"
