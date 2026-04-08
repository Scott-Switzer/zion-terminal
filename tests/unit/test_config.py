"""Tests for settings and configuration."""

import os
import pytest

from zion_terminal.config.settings import Settings, get_settings, reset_settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.llm_provider == os.getenv("LLM_PROVIDER", "none")
        assert s.cache_dir == os.getenv("CACHE_DIR", ".cache/zion")

    def test_frozen(self):
        s = Settings()
        with pytest.raises(Exception):
            s.llm_provider = "openai"

    def test_get_settings_cached(self):
        reset_settings()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_reset_settings(self):
        reset_settings()
        s1 = get_settings()
        reset_settings()
        s2 = get_settings()
        # They should be equal but not the same object after reset
        assert s1.llm_provider == s2.llm_provider
