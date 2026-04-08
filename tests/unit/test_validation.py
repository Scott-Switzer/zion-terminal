"""Tests for the validation agent."""

from src.agents.validation.agent import ValidationAgent
from src.models.responses import RetrievalResult, SynthesisResult, ValidationStatus


class TestValidationAgent:
    def setup_method(self):
        self.agent = ValidationAgent()

    def test_validate_valid_retrieval(self):
        result = RetrievalResult(
            data=[
                {
                    "ticker": "AAPL",
                    "price": 185.50,
                    "source": "yahoo_finance",
                }
            ],
            sources_used=["yahoo_finance"],
        )
        validation = self.agent.validate_retrieval(result)
        assert validation.status == ValidationStatus.PASSED
        assert validation.checks_passed > 0

    def test_validate_missing_source(self):
        result = RetrievalResult(
            data=[{"ticker": "AAPL", "price": 185}],
            sources_used=["yahoo_finance"],
        )
        validation = self.agent.validate_retrieval(result)
        assert validation.checks_failed > 0

    def test_validate_time_series_order(self):
        result = RetrievalResult(
            data=[
                {
                    "source": "yahoo_finance",
                    "data_points": [
                        {"date": "2024-01-01", "value": 100},
                        {"date": "2024-01-02", "value": 101},
                        {"date": "2024-01-03", "value": 102},
                    ],
                }
            ],
        )
        validation = self.agent.validate_retrieval(result)
        assert validation.status == ValidationStatus.PASSED

    def test_validate_empty_data(self):
        result = RetrievalResult(data=[{}])
        validation = self.agent.validate_retrieval(result)
        assert validation.checks_failed >= 1

    def test_validate_synthesis_basic(self):
        result = SynthesisResult(
            documents=[
                {
                    "type": "income_statement",
                    "data": {"total_revenue": 1_000_000, "net_income": 200_000},
                },
                {
                    "type": "balance_sheet",
                    "data": {"cash_and_equivalents": 500_000, "total_assets": 2_000_000},
                },
            ],
            entity_name="Test Corp",
            entity_ticker="TST",
        )
        validation = self.agent.validate_synthesis(result)
        assert validation.success

    def test_validate_synthesis_cash_reconciliation(self):
        result = SynthesisResult(
            documents=[
                {
                    "type": "balance_sheet",
                    "data": {"cash_and_equivalents": 500_000},
                },
                {
                    "type": "cash_flow_statement",
                    "data": {"ending_cash": 500_000},
                },
            ],
        )
        validation = self.agent.validate_synthesis(result)
        assert validation.success

    def test_validate_synthesis_cash_mismatch(self):
        result = SynthesisResult(
            documents=[
                {
                    "type": "balance_sheet",
                    "data": {"cash_and_equivalents": 500_000},
                },
                {
                    "type": "cash_flow_statement",
                    "data": {"ending_cash": 999_999},
                },
            ],
        )
        validation = self.agent.validate_synthesis(result)
        assert validation.checks_failed > 0

    def test_validate_synthesis_missing_fields(self):
        result = SynthesisResult(
            documents=[{"type": "income_statement"}],  # missing "data"
        )
        validation = self.agent.validate_synthesis(result)
        assert validation.checks_failed > 0
