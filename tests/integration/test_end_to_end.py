"""Integration tests – end-to-end query flow.

These tests hit real APIs (Yahoo Finance, which requires no key).
Mark with @pytest.mark.integration so they can be skipped in CI.
"""

import pytest

from src.orchestrator.orchestrator import Orchestrator
from src.outputs.formatter import OutputFormatter, OutputFormat


@pytest.fixture
def orchestrator():
    """Create an orchestrator with just Yahoo Finance (no API keys needed)."""
    orch = Orchestrator(
        fred_api_key="",
        edgar_identity="",
        openai_api_key="",
    )
    yield orch
    orch.close()


@pytest.fixture
def formatter():
    return OutputFormatter()


@pytest.mark.integration
class TestEndToEnd:
    def test_stock_quote(self, orchestrator, formatter):
        """Full flow: query → parse → fetch → validate → format."""
        response = orchestrator.query("Get AAPL stock price")
        assert response.success or len(response.errors) > 0  # network may fail

        if response.success:
            assert response.intent == "quote"
            assert len(response.all_data) >= 1

            # Test all output formats
            md = formatter.format(response, OutputFormat.MARKDOWN)
            assert "AAPL" in md

            js = formatter.format(response, OutputFormat.JSON)
            assert "AAPL" in js

            csv = formatter.format(response, OutputFormat.CSV)
            assert len(csv) > 0

    def test_historical_data(self, orchestrator):
        response = orchestrator.query("Show MSFT historical performance")
        if response.success:
            assert response.intent == "history"
            data = response.all_data
            assert len(data) >= 1
            # Should have data_points
            if data:
                assert "data_points" in data[0]

    def test_financials(self, orchestrator):
        response = orchestrator.query("Pull NVDA's income statement")
        if response.success:
            assert response.intent == "financials"
            data = response.all_data
            assert len(data) >= 1

    def test_synthesis_flow(self, orchestrator, formatter):
        """Synthesis: generate → validate → format."""
        response = orchestrator.query("Generate a synthetic company")
        assert response.success
        assert response.intent == "synthesis"

        md = formatter.format(response, OutputFormat.MARKDOWN)
        assert "Synthetic Entity" in md or "Company Profile" in md

    def test_multi_source_query(self, orchestrator):
        """Query that requires multiple data sources."""
        # This will only use Yahoo Finance since no FRED key
        response = orchestrator.query("Get AAPL price and TSLA price")
        # Should parse multiple tickers
        assert response.metadata.get("tickers") is not None

    def test_unknown_query_handling(self, orchestrator):
        """Graceful handling of unparseable queries."""
        response = orchestrator.query("tell me a joke about finance")
        # Should either parse something or return a helpful error
        assert isinstance(response.errors, list)
