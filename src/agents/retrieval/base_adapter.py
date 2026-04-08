"""Abstract base class for all data source adapters.

Every adapter must implement ``fetch()`` and declare which data categories
it covers so the retrieval agent can route requests correctly.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from src.cache.cache_manager import CacheManager
from src.models.responses import RetrievalResult

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Contract that every data-source adapter fulfils."""

    # Subclasses must set these
    SOURCE_NAME: str = "unknown"
    SUPPORTED_CATEGORIES: list[str] = []  # e.g. ["equities", "macro"]

    def __init__(self, cache: CacheManager | None = None) -> None:
        self._cache = cache

    # ── caching helpers ──────────────────────────────────────────

    def _cache_get(self, params: dict[str, Any]) -> Any | None:
        if self._cache is None:
            return None
        key = CacheManager.make_key(self.SOURCE_NAME, params)
        return self._cache.get(key)

    def _cache_set(self, params: dict[str, Any], value: Any) -> None:
        if self._cache is None:
            return
        key = CacheManager.make_key(self.SOURCE_NAME, params)
        self._cache.set(key, value)

    # ── abstract interface ───────────────────────────────────────

    @abstractmethod
    def fetch(self, params: dict[str, Any]) -> RetrievalResult:
        """Execute a query against this data source.

        Parameters
        ----------
        params : dict
            Source-specific parameters (ticker, series_id, form, etc.).

        Returns
        -------
        RetrievalResult
            Standardised result with ``data`` list and metadata.
        """
        ...

    def supports(self, category: str) -> bool:
        """Return True if this adapter handles *category*."""
        return category.lower() in [c.lower() for c in self.SUPPORTED_CATEGORIES]
