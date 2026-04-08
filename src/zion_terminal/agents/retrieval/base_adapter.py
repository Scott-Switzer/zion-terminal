"""Abstract base class for all data source adapters."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from zion_terminal.cache.cache_manager import CacheManager
from zion_terminal.models.responses import RetrievalResult

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    SOURCE_NAME: str = "unknown"
    SUPPORTED_CATEGORIES: list[str] = []

    def __init__(self, cache: CacheManager | None = None) -> None:
        self._cache = cache

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

    @abstractmethod
    def fetch(self, params: dict[str, Any]) -> RetrievalResult:
        ...

    def supports(self, category: str) -> bool:
        return category.lower() in [c.lower() for c in self.SUPPORTED_CATEGORIES]
