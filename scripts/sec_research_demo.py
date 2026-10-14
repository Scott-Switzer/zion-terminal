"""Reproducible existing terminal workflow: screen exact growth, then compare histories."""

import json
import os
import subprocess
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from sec_engine_acceptance import BASE, OUT, request


def main():
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    python = "/Users/scottthomasswitzer/Documents/zion-terminal-native/.venv/bin/python"
    command = [
        python,
        "-m",
        "zion_terminal.cli",
        "research",
        "screen",
        "--metrics",
        "revenue_growth",
        "--minimum",
        "0.1",
        "--ciks",
        "0000789019,0001018724,0001045810,0000104169",
        "--limit",
        "3",
        "--base-url",
        BASE,
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    screen = json.loads(result.stdout)
    assert screen["matches"]
    ciks = []
    examples = []
    for row in screen["matches"]:
        value = row["observation"]
        prior, latest = value["components"]
        expected = (
            Fraction(Decimal(latest["value_decimal"])) - Fraction(Decimal(prior["value_decimal"]))
        ) / Fraction(Decimal(prior["value_decimal"]))
        actual = Fraction(int(value["numerator"]), int(value["denominator"]))
        assert actual == expected and actual >= Fraction(1, 10)
        ciks.append(row["cik"])
        examples.append(
            {
                "cik": row["cik"],
                "label": row["label"],
                "period_end": value["period_end"],
                "growth_numerator": value["numerator"],
                "growth_denominator": value["denominator"],
                "source_ids": value["source_observation_ids"],
            }
        )
    history, measurement = request(
        "/private/v1/financial-history/batch",
        {
            "ciks": ",".join(ciks),
            "metrics": "revenue,net_income,operating_cash_flow,free_cash_flow",
            "date_from": "2020-01-01",
            "expected_release_id": screen["release_id"],
            "limit": 100,
        },
    )
    assert measurement["http_status"] == 200 and all(
        c["http_status"] == 200 for c in history["companies"]
    )
    result = {
        "status": "PASS",
        "release_id": screen["release_id"],
        "terminal_command": (
            "zion research screen --metrics revenue_growth --minimum 0.1 "
            "--ciks 0000789019,0001018724,0001045810,0000104169 "
            "--limit 3 --base-url " + BASE
        ),
        "screen_examples": examples,
        "followup_company_histories": len(history["companies"]),
        "observations_returned": sum(
            len(c["result"]["observations"]) for c in history["companies"]
        ),
        "batch_measurement": measurement,
        "qualification": "SOURCE_BACKED_RESEARCH_DEMONSTRATION;NO_INVESTMENT_RECOMMENDATION",
    }
    (OUT / "research-demo.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
