"""Tests for the Orchestrator public API and strict mode."""

import pytest
from unittest.mock import MagicMock, patch

from zion_terminal.orchestrator.orchestrator import Orchestrator
from zion_terminal.models.responses import (
    OrchestratorResponse, RetrievalResult, ValidationResult, ValidationStatus,
)


@pytest.fixture
def orc():
    """Build an orchestrator with mocked internals."""
    with patch.object(Orchestrator, "__init__", lambda self, **kw: None):
        o = Orchestrator.__new__(Orchestrator)
        o._cache = MagicMock()
        o._llm = MagicMock(name="test")
        o._strict = False
        o._retrieval = MagicMock()
        o._retrieval.available_sources = ["yahoo_finance", "sec_edgar", "fred"]
        o._validation = MagicMock()
        o._synthesis = MagicMock()
        o._parser = MagicMock()

        # Default mock returns
        o._retrieval.fetch.return_value = RetrievalResult(
            data=[{"ticker": "AAPL", "price": 150.0, "source": "yahoo_finance"}],
            sources_used=["yahoo_finance"],
        )
        o._validation.validate_retrieval.return_value = ValidationResult(
            status=ValidationStatus.PASSED, checks_run=2, checks_passed=2,
        )
        return o


class TestOrchestratorPublicMethods:
    def test_get_quote(self, orc):
        resp = orc.get_quote("AAPL")
        assert resp.success
        assert resp.intent == "quote"
        orc._retrieval.fetch.assert_called_once()

    def test_get_history(self, orc):
        resp = orc.get_history("AAPL", period="6mo", interval="1wk")
        assert resp.success
        args = orc._retrieval.fetch.call_args[0][0]
        assert args[0]["period"] == "6mo"
        assert args[0]["interval"] == "1wk"

    def test_get_financials(self, orc):
        resp = orc.get_financials("AAPL", statement_type="balance", quarterly=True)
        assert resp.success
        args = orc._retrieval.fetch.call_args[0][0]
        # Default source is SEC when available
        assert args[0]["source"] == "sec_edgar"
        assert args[0]["statement_type"] == "balance"
        assert args[0]["quarterly"] is True

    def test_get_financials_yahoo_fallback(self, orc):
        resp = orc.get_financials("AAPL", statement_type="income", source="yahoo")
        assert resp.success
        args = orc._retrieval.fetch.call_args[0][0]
        assert args[0]["source"] == "yahoo_finance"

    def test_get_filings(self, orc):
        resp = orc.get_filings("AAPL", form="10-K", limit=5)
        assert resp.success
        args = orc._retrieval.fetch.call_args[0][0]
        assert args[0]["form"] == "10-K"
        assert args[0]["limit"] == 5

    def test_get_macro(self, orc):
        resp = orc.get_macro("GDP", start_date="2020-01-01")
        assert resp.success
        args = orc._retrieval.fetch.call_args[0][0]
        assert args[0]["series_id"] == "GDP"
        assert args[0]["start_date"] == "2020-01-01"

    def test_get_info(self, orc):
        resp = orc.get_info("AAPL")
        assert resp.success
        assert resp.intent == "company_info"

    def test_get_filing_markdown(self, orc):
        resp = orc.get_filing_markdown("AAPL", form="10-Q")
        assert resp.success
        args = orc._retrieval.fetch.call_args[0][0]
        assert args[0]["action"] == "filing_markdown"
        assert args[0]["form"] == "10-Q"

    def test_get_company_facts(self, orc):
        resp = orc.get_company_facts("AAPL")
        assert resp.success
        args = orc._retrieval.fetch.call_args[0][0]
        assert args[0]["action"] == "company_facts"

    def test_ticker_uppercased(self, orc):
        orc.get_quote("aapl")
        args = orc._retrieval.fetch.call_args[0][0]
        assert args[0]["ticker"] == "AAPL"


class TestStrictMode:
    def test_strict_mode_passes_on_valid(self, orc):
        orc._strict = True
        resp = orc.get_quote("AAPL")
        assert resp.success

    def test_strict_mode_fails_on_validation_failure(self, orc):
        orc._strict = True
        orc._validation.validate_retrieval.return_value = ValidationResult(
            success=False,
            status=ValidationStatus.FAILED,
            checks_run=2, checks_passed=0, checks_failed=2,
        )
        resp = orc.get_quote("AAPL")
        assert not resp.success

    def test_non_strict_passes_on_validation_failure(self, orc):
        orc._strict = False
        orc._validation.validate_retrieval.return_value = ValidationResult(
            success=False,
            status=ValidationStatus.FAILED,
            checks_run=2, checks_passed=0, checks_failed=2,
        )
        resp = orc.get_quote("AAPL")
        # Non-strict: retrieval succeeded even though validation failed
        assert resp.success

    def test_no_validate_skips_validation(self, orc):
        resp = orc.get_quote("AAPL", validate=False)
        assert resp.success
        orc._validation.validate_retrieval.assert_not_called()


class TestLLMAssistedMetadata:
    """Verify _llm_assisted flag is surfaced in OrchestratorResponse.metadata."""

    def test_llm_assisted_surfaced_when_set(self, orc):
        from zion_terminal.orchestrator.intent_parser import ParsedIntent
        parsed = ParsedIntent(
            raw_query="some unusual query",
            intent="quote",
            tickers=["AAPL"],
            tasks=[{"source": "yahoo_finance", "ticker": "AAPL", "action": "quote"}],
        )
        parsed.params["_llm_assisted"] = True
        orc._parser.parse.return_value = parsed
        resp = orc.query("some unusual query")
        assert resp.metadata.get("llm_assisted") is True

    def test_llm_assisted_absent_when_not_set(self, orc):
        from zion_terminal.orchestrator.intent_parser import ParsedIntent
        parsed = ParsedIntent(
            raw_query="AAPL price",
            intent="quote",
            tickers=["AAPL"],
            tasks=[{"source": "yahoo_finance", "ticker": "AAPL", "action": "quote"}],
        )
        orc._parser.parse.return_value = parsed
        resp = orc.query("AAPL price")
        assert "llm_assisted" not in resp.metadata
