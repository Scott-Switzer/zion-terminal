"""Tests for data models."""

from datetime import date, datetime, timezone

from src.models.messages import AgentMessage, AgentRole, MessageType
from src.models.financial import (
    FinancialDataPoint,
    FinancialStatement,
    StatementType,
    StockQuote,
    MacroIndicator,
    SECFiling,
    FilingType,
    TimeSeriesData,
)
from src.models.responses import (
    RetrievalResult,
    SynthesisResult,
    ValidationResult,
    ValidationStatus,
    OrchestratorResponse,
)


class TestAgentMessage:
    def test_create_message(self):
        msg = AgentMessage(
            sender=AgentRole.ORCHESTRATOR,
            receiver=AgentRole.RETRIEVAL,
            message_type=MessageType.QUERY,
            payload={"ticker": "AAPL"},
        )
        assert msg.sender == "orchestrator"
        assert msg.receiver == "retrieval"
        assert msg.id is not None
        assert msg.timestamp is not None

    def test_message_with_parent(self):
        msg = AgentMessage(
            sender=AgentRole.RETRIEVAL,
            receiver=AgentRole.ORCHESTRATOR,
            message_type=MessageType.DATA_RESPONSE,
            parent_id="parent-123",
        )
        assert msg.parent_id == "parent-123"


class TestFinancialModels:
    def test_data_point(self):
        dp = FinancialDataPoint(name="Revenue", value=1_000_000, unit="USD", period="Q3 2024")
        assert dp.value == 1_000_000
        assert "Revenue" in repr(dp)

    def test_stock_quote(self):
        q = StockQuote(
            ticker="NVDA",
            company_name="NVIDIA Corp",
            price=950.50,
            volume=45_000_000,
            market_cap=2_300_000_000_000,
        )
        assert q.ticker == "NVDA"
        assert q.source == "yahoo_finance"

    def test_macro_indicator(self):
        ind = MacroIndicator(
            series_id="FEDFUNDS",
            title="Federal Funds Rate",
            value=5.33,
            unit="Percent",
            observation_date=date(2024, 1, 1),
        )
        assert ind.series_id == "FEDFUNDS"
        assert ind.source == "fred"

    def test_financial_statement(self):
        stmt = FinancialStatement(
            ticker="AAPL",
            statement_type=StatementType.INCOME,
            period="FY 2023",
            line_items={"Total Revenue": 383_285_000, "Net Income": 96_995_000},
        )
        assert stmt.get("Total Revenue") == 383_285_000
        assert stmt.get("Nonexistent") is None

    def test_sec_filing(self):
        f = SECFiling(
            ticker="MSFT",
            filing_type=FilingType.TEN_K,
            filing_date=date(2024, 7, 30),
        )
        assert f.filing_type == "10-K"

    def test_time_series(self):
        ts = TimeSeriesData(
            name="AAPL Historical",
            ticker="AAPL",
            data_points=[
                {"date": "2024-01-01", "close": 185.0},
                {"date": "2024-01-02", "close": 186.5},
            ],
        )
        assert len(ts.data_points) == 2


class TestResponseModels:
    def test_retrieval_result(self):
        r = RetrievalResult(
            data=[{"ticker": "AAPL", "price": 185}],
            sources_used=["yahoo_finance"],
        )
        assert r.success is True
        assert r.agent == "retrieval"
        assert not r.is_empty

    def test_empty_retrieval(self):
        r = RetrievalResult()
        assert r.is_empty

    def test_validation_result(self):
        v = ValidationResult(
            status=ValidationStatus.PASSED,
            checks_run=5,
            checks_passed=5,
            checks_failed=0,
        )
        assert v.pass_rate == 1.0

    def test_validation_partial(self):
        v = ValidationResult(
            status=ValidationStatus.WARNING,
            checks_run=10,
            checks_passed=8,
            checks_failed=2,
        )
        assert v.pass_rate == 0.8

    def test_synthesis_result(self):
        s = SynthesisResult(
            documents=[{"type": "income_statement", "data": {}}],
            entity_name="Test Corp",
            entity_ticker="TST",
        )
        assert s.entity_name == "Test Corp"

    def test_orchestrator_response(self):
        r = OrchestratorResponse(
            query="Get AAPL price",
            intent="quote",
            results=[
                RetrievalResult(data=[{"ticker": "AAPL", "price": 185}]),
            ],
        )
        assert len(r.all_data) == 1
