"""Tests for the validation agent."""

import pytest

from zion_terminal.agents.validation.agent import ValidationAgent
from zion_terminal.models.responses import (
    RetrievalResult,
    SynthesisResult,
    ValidationResult,
    ValidationStatus,
)


class TestValidateRetrieval:
    def setup_method(self):
        self.agent = ValidationAgent()

    def test_empty_result(self):
        result = self.agent.validate_retrieval(RetrievalResult())
        assert result.checks_run == 0
        assert result.status == ValidationStatus.PASSED

    def test_valid_stock_quote(self):
        retrieval = RetrievalResult(data=[{
            "ticker": "AAPL", "price": 185.50, "volume": 50_000_000,
            "source": "yahoo_finance",
        }])
        result = self.agent.validate_retrieval(retrieval)
        assert result.status == ValidationStatus.PASSED
        assert result.checks_failed == 0

    def test_price_out_of_bounds(self):
        retrieval = RetrievalResult(data=[{
            "ticker": "XXX", "price": -5.0, "source": "test",
        }])
        result = self.agent.validate_retrieval(retrieval)
        failed = [c for c in result.details if c.status == ValidationStatus.FAILED]
        assert any(c.check_name == "price_bounds" for c in failed)

    def test_negative_volume(self):
        retrieval = RetrievalResult(data=[{
            "ticker": "AAPL", "volume": -100, "source": "test",
        }])
        result = self.agent.validate_retrieval(retrieval)
        failed = [c for c in result.details if c.status == ValidationStatus.FAILED]
        assert any(c.check_name == "volume_non_negative" for c in failed)

    def test_missing_source(self):
        retrieval = RetrievalResult(data=[{"ticker": "AAPL", "price": 100}])
        result = self.agent.validate_retrieval(retrieval)
        failed = [c for c in result.details if c.status == ValidationStatus.FAILED]
        assert any(c.check_name == "source_attribution" for c in failed)

    def test_empty_data_item(self):
        retrieval = RetrievalResult(data=[{}])
        result = self.agent.validate_retrieval(retrieval)
        failed = [c for c in result.details if c.status == ValidationStatus.FAILED]
        assert any(c.check_name == "non_empty" for c in failed)

    def test_chronological_order_pass(self):
        retrieval = RetrievalResult(data=[{
            "source": "yahoo_finance", "type": "time_series",
            "data_points": [
                {"date": "2024-01-01", "close": 100},
                {"date": "2024-02-01", "close": 110},
                {"date": "2024-03-01", "close": 120},
            ],
        }])
        result = self.agent.validate_retrieval(retrieval)
        passed = [c for c in result.details if c.check_name == "chronological_order"]
        assert len(passed) > 0
        assert passed[0].status == ValidationStatus.PASSED

    def test_income_math_check(self):
        retrieval = RetrievalResult(data=[{
            "source": "yahoo_finance",
            "statement_type": "income_statement",
            "line_items": {
                "Total Revenue": 1_000_000,
                "Cost Of Revenue": 600_000,
                "Gross Profit": 400_000,
            },
        }])
        result = self.agent.validate_retrieval(retrieval)
        passed = [c for c in result.details if c.check_name == "income_math"]
        assert len(passed) > 0
        assert passed[0].status == ValidationStatus.PASSED


class TestValidateSynthesis:
    def setup_method(self):
        self.agent = ValidationAgent()

    def test_valid_synthesis(self):
        synthesis = SynthesisResult(documents=[
            {"type": "company_profile", "data": {"name": "Test Corp"}},
            {"type": "income_statement", "data": {
                "total_revenue": 1_000_000,
                "cost_of_revenue": 600_000,
                "gross_profit": 400_000,
            }},
            {"type": "balance_sheet", "data": {
                "total_assets": 5_000_000,
                "total_liabilities": 2_000_000,
                "total_equity": 3_000_000,
                "total_liabilities_and_equity": 5_000_000,
            }},
            {"type": "cash_flow_statement", "data": {
                "ending_cash": 800_000,
            }},
        ])
        result = self.agent.validate_synthesis(synthesis)
        assert result.checks_run > 0
        # Accounting identity should pass
        identity_checks = [c for c in result.details if c.check_name == "accounting_identity"]
        assert len(identity_checks) > 0
        assert identity_checks[0].status == ValidationStatus.PASSED

    def test_gross_profit_mismatch(self):
        synthesis = SynthesisResult(documents=[
            {"type": "income_statement", "data": {
                "total_revenue": 1_000_000,
                "cost_of_revenue": 600_000,
                "gross_profit": 500_000,  # Wrong: should be 400_000
            }},
        ])
        result = self.agent.validate_synthesis(synthesis)
        failed = [c for c in result.details if c.check_name == "gross_profit_math"]
        assert len(failed) > 0
        assert failed[0].status == ValidationStatus.FAILED

    def test_accounting_identity_failure(self):
        synthesis = SynthesisResult(documents=[
            {"type": "balance_sheet", "data": {
                "total_assets": 5_000_000,
                "total_liabilities_and_equity": 4_000_000,  # Mismatch
            }},
        ])
        result = self.agent.validate_synthesis(synthesis)
        failed = [c for c in result.details if c.check_name == "accounting_identity"]
        assert len(failed) > 0
        assert failed[0].status == ValidationStatus.FAILED
