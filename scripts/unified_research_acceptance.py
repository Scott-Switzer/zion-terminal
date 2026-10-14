"""Native owner-only live checks. Store market responses locally; print aggregates only."""

import argparse
import hashlib
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path

from sec_engine_acceptance import request


def independent(row):
    values = {k: Fraction(v["value_decimal"]) for k, v in row["inputs"].items() if v}
    metric = row["metric"]
    if metric == "revenue_growth":
        return values["revenue"] / values["prior_revenue"] - 1
    if metric == "operating_margin":
        return values["operating_income"] / values["revenue"]
    if metric == "net_margin":
        return values["net_income"] / values["revenue"]
    if metric == "free_cash_flow":
        return values["operating_cash_flow"] - values["capital_expenditures"]
    raise ValueError("UNSUPPORTED_INDEPENDENT_FORMULA")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--research-release", required=True)
    args = parser.parse_args()
    cohort = json.loads(args.cohort.read_text())["companies"]
    if len(cohort) != 20 or len({r["cik"] for r in cohort}) != 20:
        raise ValueError("EXACT_TWENTY_REQUIRED")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    checks = []
    counts = Counter()

    def call(name, route, params=None, auth=True):
        body, measure = request("/private/v1/" + route, params, auth=auth)
        target = args.output / (name + ".json")
        target.write_text(json.dumps(body, separators=(",", ":")) + "\n")
        target.chmod(0o600)
        checks.append(
            dict(
                name=name,
                **measure,
                response_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            )
        )
        return body, measure

    for item in cohort:
        cik, day = item["cik"], item["observed_on"]
        common = {"cik": cik, "date_to": day}
        overview, measure = call(cik + "-overview", "research-overview", common)
        assert measure["http_status"] == 200 and overview["observations"]
        assert overview["research_release_id"] == args.research_release
        prices, measure = call(
            cik + "-prices", "research-prices", dict(common, date_from=day, limit=2)
        )
        assert measure["http_status"] == 200 and len(prices["prices"]) == 1
        assert prices["prices"][0]["source_ids"] and prices["source_records"]
        valuation, measure = call(
            cik + "-valuation",
            "research-valuation",
            dict(common, as_of=day + "T00:00:00Z", frequency="annual", date_from="2014-01-01"),
        )
        assert measure["http_status"] == 200
        for row in valuation["observations"]:
            if row.get("value_numerator") is None:
                continue
            actual = Fraction(int(row["value_numerator"]), int(row["value_denominator"]))
            assert actual == independent(row)
            assert row["formula_version"] and row["source_ids"] and row["formula_expression"]
            counts[row["metric"]] += 1
        counts["companies"] += 1
    assert len(set(counts) - {"companies"}) >= 3
    screen, measure = call(
        "combined-screen",
        "research-screen",
        {
            "ciks": ",".join(r["cik"] for r in cohort),
            "fundamental_metric": "revenue_growth",
            "market_metric": "close",
            "limit": 1000,
        },
    )
    assert measure["http_status"] == 200 and screen["total_matches"] >= 20
    assert len({r["cik"] for r in screen["matches"]}) == 20
    for row in screen["matches"]:
        f = row["fundamental"]
        assert Fraction(int(f["value_numerator"]), int(f["value_denominator"])) == independent(f)
    peers, measure = call(
        "peers",
        "research-peers",
        {"ciks": ",".join(r["cik"] for r in cohort), "metrics": "operating_margin,net_margin"},
    )
    assert measure["http_status"] == 200 and peers["observations"]
    for name, route, params, status, auth in (
        ("unauthorized", "research-overview", {"cik": cohort[0]["cik"]}, 401, False),
        ("invalid-cik", "research-overview", {"cik": "bad"}, 422, True),
        (
            "price-pit-rejected",
            "research-prices",
            {"symbol": cohort[0]["symbol"], "as_of": "2019-12-31T00:00:00Z"},
            422,
            True,
        ),
        (
            "unverified-marketcap",
            "research-valuation",
            {"cik": cohort[0]["cik"], "metrics": "market_cap"},
            200,
            True,
        ),
    ):
        body, measure = call(name, route, params, auth)
        assert measure["http_status"] == status
        if name == "unverified-marketcap":
            assert all(r["value_numerator"] is None for r in body["observations"])
    report = dict(
        status="PASS",
        research_release_id=args.research_release,
        counts=dict(counts),
        screen_companies=20,
        requests=checks,
        provider_rows_printed=False,
    )
    (args.output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ("status", "counts", "screen_companies", "research_release_id")}
        )
    )


if __name__ == "__main__":
    main()
