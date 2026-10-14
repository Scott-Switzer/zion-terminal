import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from sec_engine import VERSION, history, normalize_sources, statement_policy

sys.path.insert(0, str(Path(__file__).parent))
from test_sec_history import fact, source


def test_outside_pilot_cik_has_generic_normalization_and_history():
    c = source([fact()])
    c["cik"] = 789019
    d = normalize_sources(c, "0000789019", "a" * 64)
    r = history(
        d,
        metrics=["revenue"],
        frequency="annual",
        date_from="2000-01-01",
        date_to="2025-01-01",
        as_of="2025-01-01T00:00:00Z",
    )
    assert r["observations"][0]["value_decimal"] == "100"
    assert d["schema_version"] == VERSION


def test_bank_statement_evidence_does_not_enable_industrial_fcf_or_debt():
    c = source([fact()])
    c["cik"] = 19617
    c["facts"]["us-gaap"]["InterestIncomeExpenseAfterProvisionForLoanLosses"] = {"units": {}}
    p = statement_policy(c)
    assert p["family"] == "financial_statement_evidence"
    assert "free_cash_flow" in p["quarantined_metrics"]
    assert "total_debt" in p["quarantined_metrics"]


def test_generic_debt_never_guesses_zero_components():
    c = source([fact()])
    c["cik"] = 789019
    p = statement_policy(c)
    assert "total_debt" in p["quarantined_metrics"]


def test_same_end_different_annual_contexts_are_explicit_ambiguity():
    c = source(
        [fact(start="2020-01-01", end="2020-12-31"), fact(start="2020-01-03", end="2020-12-31")]
    )
    c["cik"] = 789019
    d = normalize_sources(c, "0000789019", "a" * 64)
    r = history(
        d,
        metrics=["revenue"],
        frequency="annual",
        date_from="2020-01-01",
        date_to="2021-01-01",
        as_of="2025-01-01T00:00:00Z",
    )
    assert not r["observations"]
    assert any(x["reason"] == "OVERLAPPING_FISCAL_CONTEXTS" for x in r["coverage_gaps"])


def test_balance_identity_checks_exact_same_date_scope_and_source_links():
    from sec_engine import balance_checks

    c = source([fact()])
    c["cik"] = 789019
    for tag, val in [("Assets", 100), ("LiabilitiesAndStockholdersEquity", 101)]:
        c["facts"]["us-gaap"][tag] = {
            "units": {"USD": [fact(val=val, start=None, end="2020-12-31")]}
        }
    d = normalize_sources(c, "0000789019", "a" * 64)
    checks = balance_checks(d, "2025-01-01T00:00:00Z", "2020-01-01", "2021-01-01")
    assert checks[0]["status"] == "FAIL"
    assert checks[0]["difference_decimal"] == "-1"
    assert len(checks[0]["source_observation_ids"]) == 2


def test_financial_tags_do_not_misclassify_nonoperating_interest_as_bank():
    c = source([fact()])
    c["facts"]["us-gaap"]["InterestIncomeExpenseNet"] = {"units": {}}
    assert statement_policy(c)["family"] == "general_us_gaap_unclassified"


def test_sixteen_week_quarter_requires_contiguous_annual_calendar_evidence():
    c = source([fact(start="2020-01-01", end="2020-12-29")])
    c["cik"] = 789019
    c["facts"]["us-gaap"]["Revenues"]["units"]["USD"] += [
        fact(start="2020-01-01", end="2020-03-24", val=20) | {"form": "10-Q"},
        fact(start="2020-01-01", end="2020-06-16", val=40) | {"form": "10-Q"},
        fact(start="2020-01-01", end="2020-09-08", val=60) | {"form": "10-Q"},
    ]
    d = normalize_sources(c, "0000789019", "a" * 64)
    r = history(
        d,
        metrics=["revenue"],
        frequency="quarterly",
        date_from="2020-01-01",
        date_to="2021-01-01",
        as_of="2025-01-01T00:00:00Z",
    )
    assert any(
        x["period_start"] == "2020-09-09" and x["period_end"] == "2020-12-29"
        for x in r["observations"]
    )


def test_ambiguous_operating_cash_flow_cannot_produce_annual_fcf():
    c = source([])
    c["cik"] = 789019
    c["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"] = {
        "units": {
            "USD": [
                fact(start="2020-01-01", end="2020-12-31", val=100),
                fact(start="2020-01-03", end="2020-12-31", val=101),
            ]
        }
    }
    c["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"] = {
        "units": {"USD": [fact(start="2020-01-01", end="2020-12-31", val=20)]}
    }
    d = normalize_sources(c, "0000789019", "a" * 64)
    r = history(
        d,
        metrics=["operating_cash_flow", "free_cash_flow"],
        frequency="annual",
        date_from="2020-01-01",
        date_to="2021-01-01",
        as_of="2025-01-01T00:00:00Z",
    )
    assert not r["observations"]


def test_ambiguous_quarter_cannot_feed_fcf_or_ttm_or_cumulative_differences():
    c = source([])
    c["cik"] = 789019
    periods = [
        ("2020-01-01", "2020-03-31"),
        ("2020-04-01", "2020-06-30"),
        ("2020-07-01", "2020-09-30"),
        ("2020-10-01", "2020-12-31"),
    ]
    cfo = [fact(start=s, end=e, val=30) | {"form": "10-Q"} for s, e in periods]
    cfo += [
        fact(start="2020-01-03", end="2020-03-31", val=31) | {"form": "10-Q"},
        fact(start="2020-01-01", end="2020-06-30", val=60) | {"form": "10-Q"},
    ]
    c["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"] = {"units": {"USD": cfo}}
    c["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"] = {
        "units": {"USD": [fact(start=s, end=e, val=10) | {"form": "10-Q"} for s, e in periods]}
    }
    d = normalize_sources(c, "0000789019", "a" * 64)
    for frequency in ["quarterly", "ttm"]:
        r = history(
            d,
            metrics=["operating_cash_flow", "free_cash_flow"],
            frequency=frequency,
            date_from="2020-01-01",
            date_to="2021-01-01",
            as_of="2025-01-01T00:00:00Z",
        )
        assert not any(
            x["metric"] == "free_cash_flow"
            and (frequency == "ttm" or x["period_end"] == "2020-03-31")
            for x in r["observations"]
        )


def test_future_period_relative_to_source_filing_is_not_a_valid_financial_observation():
    c = source([fact(end="2201-08-31", filed="2021-01-01")])
    c["cik"] = 789019
    d = normalize_sources(c, "0000789019", "a" * 64)
    assert not d["observations"]
    assert d["rejections"]["PERIOD_END_AFTER_SOURCE_FILING_DATE"] == 1
