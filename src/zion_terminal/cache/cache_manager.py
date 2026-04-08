"""File-based caching with TTL support using diskcache.

Cache keys include a schema version so that format changes automatically
invalidate stale entries without requiring a manual cache clear.
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
_DEFAULT_TTL = 3600

# Bump this when any adapter's output schema changes.
# Old cached payloads will be ignored (key mismatch).
CACHE_SCHEMA_VERSION = 1


class CacheManager:
    def __init__(self, cache_dir: str | Path | None = None, default_ttl: int = _DEFAULT_TTL) -> None:
        self._dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache = Cache(str(self._dir))
        self._default_ttl = default_ttl

    @staticmethod
    def make_key(source: str, params: dict[str, Any]) -> str:
        raw = json.dumps(
            {"_v": CACHE_SCHEMA_VERSION, "source": source, **params},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._cache.set(key, value, expire=ttl or self._default_ttl)

    def invalidate(self, key: str) -> bool:
        return self._cache.delete(key)

    def clear(self) -> None:
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "directory": str(self._dir),
            "size_bytes": self._cache.volume(),
            "entry_count": len(self._cache),
            "default_ttl": self._default_ttl,
        }

    def close(self) -> None:
        self._cache.close()
