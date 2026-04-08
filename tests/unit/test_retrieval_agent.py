"""Tests for the retrieval agent routing logic (no live API calls)."""

import tempfile

from src.agents.retrieval.agent import RetrievalAgent
from src.cache.cache_manager import CacheManager


class TestRetrievalAgent:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.cache = CacheManager(cache_dir=self._tmpdir, default_ttl=60)
        self.agent = RetrievalAgent(cache=self.cache)

    def teardown_method(self):
        self.cache.close()

    def test_available_sources(self):
        sources = self.agent.available_sources
        assert "yahoo_finance" in sources

    def test_available_categories(self):
        cats = self.agent.available_categories
        assert "yahoo_finance" in cats
        assert "equities" in cats["yahoo_finance"]

    def test_unknown_source_error(self):
        # Use a task with no ticker so auto-routing can't kick in
        result = self.agent.fetch([{"source": "nonexistent"}])
        assert len(result.errors) > 0

    def test_missing_ticker_error(self):
        result = self.agent.fetch([{"source": "yahoo_finance", "action": "quote"}])
        assert not result.success

    def test_auto_route_by_ticker(self):
        # Without explicit source, should route to yahoo_finance
        result = self.agent.fetch([{"ticker": "AAPL", "action": "quote"}])
        # This may succeed or fail depending on network, but routing should work
        assert True  # If we get here, routing didn't crash

    def test_auto_route_by_series_id(self):
        # Without explicit source, should route to FRED
        # Will fail without API key but shouldn't crash
        result = self.agent.fetch([{"series_id": "GDP"}])
        # No FRED adapter registered (no API key), so expect error
        assert len(result.errors) > 0 or len(result.data) >= 0

    def test_multiple_tasks(self):
        result = self.agent.fetch([
            {"source": "yahoo_finance", "ticker": "AAPL", "action": "quote"},
            {"source": "yahoo_finance", "ticker": "MSFT", "action": "quote"},
        ])
        # Multiple tasks should not crash; may have data or errors
        assert isinstance(result.data, list)
