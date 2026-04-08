"""LLM provider abstraction.

Supports three modes:
  - none  : no LLM calls, all features degrade gracefully
  - openai: OpenAI API (requires OPENAI_API_KEY)
  - ollama: local Ollama server via OpenAI-compatible API
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """Contract for all LLM providers."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider can serve requests right now."""
        ...

    @abstractmethod
    def complete(self, system: str, user: str, temperature: float = 0.0, max_tokens: int = 500) -> str | None:
        """Send a chat completion request. Returns the text response or None on failure."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class NoLLMProvider(BaseLLMProvider):
    """Stub provider — all calls return None. Core retrieval works without LLM."""

    @property
    def is_available(self) -> bool:
        return True  # always "available" — just returns nothing

    @property
    def name(self) -> str:
        return "none"

    def complete(self, system: str, user: str, **kwargs) -> str | None:
        logger.debug("NoLLMProvider: skipping LLM call (no provider configured)")
        return None


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self._api_key)
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install 'zion-terminal[openai]'"
                )
        return self._client

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    @property
    def name(self) -> str:
        return "openai"

    def complete(self, system: str, user: str, temperature: float = 0.0, max_tokens: int = 500) -> str | None:
        if not self.is_available:
            return None
        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as exc:
            logger.warning("OpenAI call failed: %s", exc)
            return None


class OllamaProvider(BaseLLMProvider):
    """Ollama via its OpenAI-compatible API endpoint."""

    def __init__(self, base_url: str = "http://localhost:11434/v1", model: str = "llama3.1") -> None:
        self._base_url = base_url
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(base_url=self._base_url, api_key="ollama")
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install 'zion-terminal[ollama]'"
                )
        return self._client

    @property
    def is_available(self) -> bool:
        return True  # availability checked on first actual call

    @property
    def name(self) -> str:
        return "ollama"

    def complete(self, system: str, user: str, temperature: float = 0.0, max_tokens: int = 500) -> str | None:
        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as exc:
            logger.warning("Ollama call failed: %s", exc)
            return None


def build_provider(
    provider_name: str = "none",
    openai_api_key: str = "",
    openai_model: str = "gpt-4o-mini",
    ollama_base_url: str = "http://localhost:11434/v1",
    ollama_model: str = "llama3.1",
) -> BaseLLMProvider:
    """Factory: build the right provider from config."""
    name = provider_name.lower().strip()
    if name == "openai":
        if not openai_api_key:
            logger.warning("LLM_PROVIDER=openai but OPENAI_API_KEY is empty — falling back to none")
            return NoLLMProvider()
        return OpenAIProvider(api_key=openai_api_key, model=openai_model)
    elif name == "ollama":
        return OllamaProvider(base_url=ollama_base_url, model=ollama_model)
    else:
        return NoLLMProvider()
