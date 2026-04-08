"""Tests for the cache manager."""

import os
import tempfile
import pytest

from zion_terminal.cache.cache_manager import CacheManager


class TestCacheManager:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.cache = CacheManager(cache_dir=self._tmpdir, default_ttl=60)

    def teardown_method(self):
        self.cache.close()

    def test_set_and_get(self):
        self.cache.set("key1", {"data": "test"})
        result = self.cache.get("key1")
        assert result == {"data": "test"}

    def test_get_missing_key(self):
        result = self.cache.get("nonexistent")
        assert result is None

    def test_clear(self):
        self.cache.set("key1", "value1")
        self.cache.clear()
        assert self.cache.get("key1") is None

    def test_custom_ttl(self):
        self.cache.set("key2", "value2", ttl=1)
        result = self.cache.get("key2")
        assert result == "value2"

    def test_overwrite(self):
        self.cache.set("key1", "old")
        self.cache.set("key1", "new")
        assert self.cache.get("key1") == "new"
