"""Centralised settings loaded from environment / .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

_project_root = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(_project_root / ".env")


class Settings(BaseModel):
    """Application-wide settings. All fields have safe defaults."""

    # ── LLM provider ─────────────────────────────────────────────
    llm_provider: str = Field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "none"),
        description="none | openai | ollama",
    )
    openai_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    ollama_base_url: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    )
    ollama_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1")
    )

    # ── Data sources ─────────────────────────────────────────────
    fred_api_key: str = Field(default_factory=lambda: os.getenv("FRED_API_KEY", ""))
    edgar_identity: str = Field(
        default_factory=lambda: os.getenv("EDGAR_IDENTITY", "")
    )

    # ── Cache ────────────────────────────────────────────────────
    cache_dir: str = Field(default_factory=lambda: os.getenv("CACHE_DIR", ".cache/zion"))
    cache_ttl: int = Field(default_factory=lambda: int(os.getenv("CACHE_TTL_SECONDS", "3600")))

    # ── Logging ──────────────────────────────────────────────────
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "WARNING"))

    model_config = {"frozen": True}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Clear the cached singleton (useful in tests)."""
    get_settings.cache_clear()
