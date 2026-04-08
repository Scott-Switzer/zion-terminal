"""Tests for the LLM provider abstraction."""

import pytest

from zion_terminal.providers.base import (
    BaseLLMProvider,
    NoLLMProvider,
    OpenAIProvider,
    OllamaProvider,
    build_provider,
)


class TestNoLLMProvider:
    def test_always_available(self):
        p = NoLLMProvider()
        assert p.is_available is True

    def test_name(self):
        assert NoLLMProvider().name == "none"

    def test_complete_returns_none(self):
        p = NoLLMProvider()
        assert p.complete(system="test", user="test") is None

    def test_is_base_provider(self):
        assert isinstance(NoLLMProvider(), BaseLLMProvider)


class TestOpenAIProvider:
    def test_not_available_without_key(self):
        p = OpenAIProvider(api_key="")
        assert p.is_available is False

    def test_available_with_key(self):
        p = OpenAIProvider(api_key="sk-test123")
        assert p.is_available is True

    def test_name(self):
        assert OpenAIProvider(api_key="test").name == "openai"

    def test_complete_returns_none_without_key(self):
        p = OpenAIProvider(api_key="")
        assert p.complete(system="test", user="test") is None


class TestOllamaProvider:
    def test_always_available(self):
        p = OllamaProvider()
        assert p.is_available is True

    def test_name(self):
        assert OllamaProvider().name == "ollama"

    def test_custom_base_url(self):
        p = OllamaProvider(base_url="http://myhost:11434/v1")
        assert p._base_url == "http://myhost:11434/v1"


class TestBuildProvider:
    def test_default_is_none(self):
        p = build_provider()
        assert isinstance(p, NoLLMProvider)

    def test_none_explicit(self):
        p = build_provider(provider_name="none")
        assert isinstance(p, NoLLMProvider)

    def test_openai_without_key_falls_back(self):
        p = build_provider(provider_name="openai", openai_api_key="")
        assert isinstance(p, NoLLMProvider)

    def test_openai_with_key(self):
        p = build_provider(provider_name="openai", openai_api_key="sk-test")
        assert isinstance(p, OpenAIProvider)

    def test_ollama(self):
        p = build_provider(provider_name="ollama")
        assert isinstance(p, OllamaProvider)

    def test_unknown_falls_back(self):
        p = build_provider(provider_name="anthropic")
        assert isinstance(p, NoLLMProvider)

    def test_case_insensitive(self):
        p = build_provider(provider_name="OpenAI", openai_api_key="sk-test")
        assert isinstance(p, OpenAIProvider)
