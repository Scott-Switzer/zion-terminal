"""Tests for data models and response schemas."""

import pytest

from zion_terminal.models.financial import (
    SECFiling,
    FilingType,
    FinancialStatement,
    MacroIndicator,
    StatementType,
    StockQuote,
    TimeSeriesData,
)
from zion_terminal.models.responses import (
    AgentResponse,
    OrchestratorResponse,
    RetrievalResult,
    SynthesisResult,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


class TestStockQuote:
    def test_defaults(self):
        q = StockQuote(ticker="AAPL")
        assert q.ticker == "AAPL"
        assert q.price is None
        assert q.source == "yahoo_finance"

    def test_full_quote(self):
        q = StockQuote(
            ticker="AAPL", company_name="Apple Inc.",
            price=185.50, volume=50_000_000,
            market_cap=2_900_000_000_000,
        )
        assert q.price == 185.50
        assert q.volume == 50_000_000


class TestFinancialStatement:
    def test_get_line_item(self):
        stmt = FinancialStatement(
            ticker="AAPL", statement_type=StatementType.INCOME,
            period="2024-Q4", line_items={"Total Revenue": 119_000_000_000},
        )
        assert stmt.get("Total Revenue") == 119_000_000_000
        assert stmt.get("Nonexistent", 0) == 0


class TestSECFiling:
    def test_content_markdown_field(self):
        f = SECFiling(
            ticker="AAPL", filing_type=FilingType.TEN_K,
            content_markdown="# Annual Report\n\nContent here.",
        )
        assert f.content_markdown is not None
        assert "Annual Report" in f.content_markdown


class TestRetrievalResult:
    def test_is_empty(self):
        r = RetrievalResult()
        assert r.is_empty is True

    def test_not_empty(self):
        r = RetrievalResult(data=[{"ticker": "AAPL", "price": 185}])
        assert r.is_empty is False

    def test_defaults(self):
        r = RetrievalResult()
        assert r.agent == "retrieval"
        assert r.success is True
        assert r.cached is False


class TestValidationResult:
    def test_pass_rate_no_checks(self):
        v = ValidationResult()
        assert v.pass_rate == 1.0

    def test_pass_rate_with_checks(self):
        v = ValidationResult(checks_run=10, checks_passed=8, checks_failed=2)
        assert v.pass_rate == 0.8


class TestValidationCheck:
    def test_fields(self):
        c = ValidationCheck(
            check_name="price_bounds", status=ValidationStatus.PASSED,
            message="price within bounds", field="item[0].price",
        )
        assert c.check_name == "price_bounds"
        assert c.status == ValidationStatus.PASSED


class TestOrchestratorResponse:
    def test_all_data_with_retrieval(self):
        r = OrchestratorResponse(
            results=[RetrievalResult(data=[{"a": 1}, {"b": 2}])]
        )
        assert len(r.all_data) == 2

    def test_all_data_with_synthesis(self):
        r = OrchestratorResponse(
            results=[SynthesisResult(documents=[{"type": "test", "data": {}}])]
        )
        assert len(r.all_data) == 1

    def test_all_data_empty(self):
        r = OrchestratorResponse()
        assert r.all_data == []
