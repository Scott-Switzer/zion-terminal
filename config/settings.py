"""Centralised settings loaded from environment / .env file.

Uses pydantic-settings for validation.  Every component imports
``get_settings()`` to read configuration.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")


class Settings(BaseModel):
    """Application-wide settings (immutable after creation)."""

    # LLM
    openai_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    # FRED
    fred_api_key: str = Field(default_factory=lambda: os.getenv("FRED_API_KEY", ""))

    # Alpha Vantage
    alpha_vantage_api_key: str = Field(
        default_factory=lambda: os.getenv("ALPHA_VANTAGE_API_KEY", "")
    )

    # Polygon
    polygon_api_key: str = Field(default_factory=lambda: os.getenv("POLYGON_API_KEY", ""))

    # SEC EDGAR
    edgar_identity: str = Field(
        default_factory=lambda: os.getenv("EDGAR_IDENTITY", "Zion Terminal user@example.com")
    )

    # Cache
    cache_dir: str = Field(default_factory=lambda: os.getenv("CACHE_DIR", ".cache/zion"))
    cache_ttl: int = Field(
        default_factory=lambda: int(os.getenv("CACHE_TTL_SECONDS", "3600"))
    )

    # Logging
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    class Config:
        frozen = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor – settings are built once then reused."""
    return Settings()
