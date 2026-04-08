"""Tests for the cache manager."""

import os
import tempfile
from unittest.mock import patch

import pytest

from zion_terminal.cache import cache_manager
from zion_terminal.cache.cache_manager import CacheManager, CACHE_SCHEMA_VERSION


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


class TestCacheVersioning:
    def test_make_key_includes_version(self):
        key1 = CacheManager.make_key("test", {"a": 1})
        assert isinstance(key1, str)
        assert len(key1) == 64  # SHA-256 hex

    def test_different_version_different_key(self):
        key_v1 = CacheManager.make_key("test", {"a": 1})
        # Simulate a version bump
        with patch.object(cache_manager, "CACHE_SCHEMA_VERSION", CACHE_SCHEMA_VERSION + 1):
            key_v2 = CacheManager.make_key("test", {"a": 1})
        assert key_v1 != key_v2

    def test_same_params_same_key(self):
        key1 = CacheManager.make_key("test", {"a": 1, "b": 2})
        key2 = CacheManager.make_key("test", {"b": 2, "a": 1})  # different order
        assert key1 == key2  # sort_keys=True

    def test_different_params_different_key(self):
        key1 = CacheManager.make_key("fred", {"series_id": "GDP"})
        key2 = CacheManager.make_key("fred", {"series_id": "GDP", "start_date": "2020-01-01"})
        assert key1 != key2  # FRED cache bug fix verification
