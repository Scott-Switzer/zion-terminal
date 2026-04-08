"""Tests for the caching layer."""

import tempfile
from pathlib import Path

from src.cache.cache_manager import CacheManager


class TestCacheManager:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.cache = CacheManager(cache_dir=self._tmpdir, default_ttl=60)

    def teardown_method(self):
        self.cache.close()

    def test_make_key_deterministic(self):
        k1 = CacheManager.make_key("yahoo", {"ticker": "AAPL"})
        k2 = CacheManager.make_key("yahoo", {"ticker": "AAPL"})
        assert k1 == k2

    def test_make_key_differs(self):
        k1 = CacheManager.make_key("yahoo", {"ticker": "AAPL"})
        k2 = CacheManager.make_key("yahoo", {"ticker": "NVDA"})
        assert k1 != k2

    def test_get_miss(self):
        assert self.cache.get("nonexistent") is None

    def test_set_and_get(self):
        self.cache.set("key1", {"data": [1, 2, 3]})
        result = self.cache.get("key1")
        assert result == {"data": [1, 2, 3]}

    def test_invalidate(self):
        self.cache.set("key2", "value")
        assert self.cache.get("key2") == "value"
        self.cache.invalidate("key2")
        assert self.cache.get("key2") is None

    def test_clear(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.clear()
        assert self.cache.get("a") is None
        assert self.cache.get("b") is None

    def test_stats(self):
        self.cache.set("x", 42)
        stats = self.cache.stats()
        assert stats["entry_count"] >= 1
        assert stats["default_ttl"] == 60
