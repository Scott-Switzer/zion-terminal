"""Tests for the retrieval agent with mocked adapters."""

import pytest

from zion_terminal.agents.retrieval.agent import RetrievalAgent
from zion_terminal.agents.retrieval.base_adapter import BaseAdapter
from zion_terminal.models.responses import RetrievalResult


class MockAdapter(BaseAdapter):
    SOURCE_NAME = "mock"
    SUPPORTED_CATEGORIES = ["test"]

    def __init__(self, data=None, error=None, **kwargs):
        super().__init__(**kwargs)
        self._data = data or []
        self._error = error

    def fetch(self, params):
        if self._error:
            return RetrievalResult(success=False, errors=[self._error])
        return RetrievalResult(data=self._data, sources_used=[self.SOURCE_NAME])


class TestRetrievalAgent:
    def test_register_and_list_sources(self):
        agent = RetrievalAgent()
        mock = MockAdapter(data=[{"test": True}])
        agent.register_adapter(mock)
        assert "mock" in agent.available_sources

    def test_fetch_with_mock(self):
        agent = RetrievalAgent()
        mock = MockAdapter(data=[{"ticker": "TEST", "price": 42, "source": "mock"}])
        agent.register_adapter(mock)
        result = agent.fetch([{"source": "mock", "ticker": "TEST"}])
        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0]["price"] == 42

    def test_missing_adapter(self):
        agent = RetrievalAgent()
        result = agent.fetch([{"source": "nonexistent"}])
        assert len(result.errors) > 0

    def test_fetch_error_handling(self):
        agent = RetrievalAgent()
        mock = MockAdapter(error="Something failed")
        agent.register_adapter(mock)
        result = agent.fetch([{"source": "mock"}])
        assert "Something failed" in result.errors

    def test_yahoo_finance_always_registered(self):
        agent = RetrievalAgent()
        assert "yahoo_finance" in agent.available_sources

    def test_fred_not_registered_without_key(self):
        agent = RetrievalAgent(fred_api_key="")
        assert "fred" not in agent.available_sources

    def test_fred_registered_with_key(self):
        agent = RetrievalAgent(fred_api_key="test_key")
        assert "fred" in agent.available_sources

    def test_sec_not_registered_without_identity(self):
        agent = RetrievalAgent(edgar_identity="")
        assert "sec_edgar" not in agent.available_sources

    def test_sec_registered_with_identity(self):
        agent = RetrievalAgent(edgar_identity="Test User test@example.com")
        assert "sec_edgar" in agent.available_sources

    def test_multiple_tasks(self):
        agent = RetrievalAgent()
        mock = MockAdapter(data=[{"v": 1, "source": "mock"}])
        agent.register_adapter(mock)
        result = agent.fetch([
            {"source": "mock", "a": 1},
            {"source": "mock", "b": 2},
        ])
        assert len(result.data) == 2

    def test_auto_route_ticker_to_yahoo(self):
        """Tasks with 'ticker' and no explicit source should route to yahoo_finance."""
        agent = RetrievalAgent()
        # This should find yahoo_finance adapter
        adapter = agent._find_adapter_for_task({"ticker": "AAPL", "action": "quote"})
        assert adapter is not None
        assert adapter.SOURCE_NAME == "yahoo_finance"

    def test_auto_route_series_to_fred(self):
        agent = RetrievalAgent(fred_api_key="test")
        adapter = agent._find_adapter_for_task({"series_id": "GDP"})
        assert adapter is not None
        assert adapter.SOURCE_NAME == "fred"

    def test_auto_route_filings_to_sec(self):
        agent = RetrievalAgent(edgar_identity="test user@test.com")
        adapter = agent._find_adapter_for_task({"ticker": "AAPL", "action": "filings"})
        assert adapter is not None
        assert adapter.SOURCE_NAME == "sec_edgar"
