"""Integration tests – require network access. Deselect with: pytest -m 'not integration'."""

import pytest

from zion_terminal.orchestrator.orchestrator import Orchestrator
from zion_terminal.outputs.formatter import OutputFormat, format_response


@pytest.fixture
def orchestrator():
    """Orchestrator with no LLM, no FRED key, no EDGAR identity — just Yahoo Finance."""
    orc = Orchestrator()
    yield orc
    orc.close()


@pytest.mark.integration
class TestEndToEnd:
    def test_query_aapl_price(self, orchestrator):
        resp = orchestrator.query("Get AAPL stock price")
        assert resp.success is True
        assert len(resp.all_data) > 0
        # Should contain a price
        data = resp.all_data[0]
        assert "price" in data or "close" in data

    def test_query_aapl_history(self, orchestrator):
        resp = orchestrator.query("AAPL historical prices last 1 month daily")
        assert resp.success is True
        assert len(resp.all_data) > 0

    def test_query_company_name(self, orchestrator):
        resp = orchestrator.query("Apple stock price")
        assert resp.success is True
        assert len(resp.all_data) > 0

    def test_query_synthesis(self, orchestrator):
        resp = orchestrator.query("generate synthetic company")
        assert resp.success is True
        types = [d.get("type") for d in resp.all_data]
        assert "income_statement" in types

    def test_unknown_query(self, orchestrator):
        resp = orchestrator.query("hello world")
        assert resp.success is False

    def test_format_markdown(self, orchestrator):
        resp = orchestrator.query("Get AAPL stock price")
        md = format_response(resp, OutputFormat.MARKDOWN)
        assert "AAPL" in md

    def test_format_json(self, orchestrator):
        resp = orchestrator.query("Get AAPL stock price")
        j = format_response(resp, OutputFormat.JSON)
        import json
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_format_csv(self, orchestrator):
        resp = orchestrator.query("Get AAPL stock price")
        c = format_response(resp, OutputFormat.CSV)
        assert "ticker" in c or "AAPL" in c
