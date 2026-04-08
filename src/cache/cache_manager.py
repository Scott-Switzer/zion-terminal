"""File-based caching with TTL support.

Uses diskcache for reliable concurrent-safe file caching.  Every adapter
call is keyed on (source, query_params) so identical requests hit cache
instead of burning API quota.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from diskcache import Cache

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path(".cache/zion")
_DEFAULT_TTL = 3600  # 1 hour


class CacheManager:
    """Thin wrapper around diskcache with JSON-serialisable values."""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        default_ttl: int = _DEFAULT_TTL,
    ) -> None:
        self._dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache = Cache(str(self._dir))
        self._default_ttl = default_ttl
        logger.debug("Cache initialised at %s (TTL=%ds)", self._dir, default_ttl)

    # ── public API ───────────────────────────────────────────────

    @staticmethod
    def make_key(source: str, params: dict[str, Any]) -> str:
        """Deterministic cache key from source name + query parameters."""
        raw = json.dumps({"source": source, **params}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        """Return cached value or ``None`` on miss."""
        val = self._cache.get(key)
        if val is not None:
            logger.debug("Cache HIT  key=%s…", key[:12])
        else:
            logger.debug("Cache MISS key=%s…", key[:12])
        return val

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store *value* under *key* with an optional per-entry TTL."""
        self._cache.set(key, value, expire=ttl or self._default_ttl)

    def invalidate(self, key: str) -> bool:
        """Remove a single entry.  Returns True if the key existed."""
        return self._cache.delete(key)

    def clear(self) -> None:
        """Wipe every cached entry."""
        self._cache.clear()
        logger.info("Cache cleared")

    def stats(self) -> dict[str, Any]:
        """Basic cache statistics."""
        return {
            "directory": str(self._dir),
            "size_bytes": self._cache.volume(),
            "entry_count": len(self._cache),
            "default_ttl": self._default_ttl,
        }

    def close(self) -> None:
        self._cache.close()
