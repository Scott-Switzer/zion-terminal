import importlib.util
import sys
from fractions import Fraction
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "unified_acceptance", SCRIPTS / "unified_research_acceptance.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.mark.parametrize(
    "metric,inputs,expected",
    [
        ("revenue_growth", {"revenue": "11", "prior_revenue": "3"}, Fraction(8, 3)),
        ("operating_margin", {"revenue": "7", "operating_income": "-2"}, Fraction(-2, 7)),
        ("net_margin", {"revenue": "10", "net_income": "0.1"}, Fraction(1, 100)),
        (
            "free_cash_flow",
            {"operating_cash_flow": "2", "capital_expenditures": "3.25"},
            Fraction(-5, 4),
        ),
    ],
)
def test_independent_exact_negative_and_nonterminating_calculations(metric, inputs, expected):
    row = {"metric": metric, "inputs": {k: {"value_decimal": v} for k, v in inputs.items()}}
    assert m.independent(row) == expected
