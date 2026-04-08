# Runbook: Cache Invalidation

The Zion Terminal cache is a local disk cache managed by `CacheManager` in `src/zion_terminal/cache/cache_manager.py`. This runbook describes how to invalidate cached data in various scenarios.

---

## Cache Location

By default, cached data is stored at:

```
.cache/zion/
```

This path is relative to the working directory when the CLI is invoked. It can be overridden by setting `ZION_CACHE_DIR` in the environment.

---

## Method 1: Manual Deletion

**When to use:** You need to force a full re-fetch of all data immediately.

```bash
# Delete the entire cache directory
rm -rf .cache/zion/

# Or delete a specific adapter's cache
rm -rf .cache/zion/sec_edgar/
rm -rf .cache/zion/yahoo_finance/
```

The cache directory will be re-created automatically on the next run.

**Caution:** This invalidates all cached results across all adapters. Use targeted deletion (per-adapter subdirectory) when possible.

---

## Method 2: Schema Version Bump

**When to use:** The output format of one or more adapters has changed. Old cache entries have a different shape than what the current code expects.

In `src/zion_terminal/cache/cache_manager.py`, increment the version constant:

```python
# Before
CACHE_SCHEMA_VERSION = 4

# After
CACHE_SCHEMA_VERSION = 5
```

The cache manager includes the schema version in every cache key. When the version is bumped, all old keys no longer match and entries are treated as cache misses. Old files accumulate on disk — run a manual cleanup after bumping:

```bash
rm -rf .cache/zion/
```

**Commit message convention:** Include the bump in your commit: `Bump CACHE_SCHEMA_VERSION to 5 — MyAdapter output format change`.

---

## Method 3: TTL-based Expiry

**When to use:** You want cached entries to expire automatically after a set time (e.g. market data that goes stale after 15 minutes).

Configure `CACHE_TTL_SECONDS` in `cache_manager.py` or via environment variable:

```python
CACHE_TTL_SECONDS = int(os.environ.get("ZION_CACHE_TTL", 900))  # 15 minutes default
```

Or at runtime:

```bash
ZION_CACHE_TTL=3600 zion get-price AAPL  # 1-hour TTL for this run
```

`ZION_CACHE_TTL=0` disables TTL-based expiry (cache entries live indefinitely).

**Note:** TTL is checked at read time. Expired entries are treated as cache misses; the stale file remains on disk until the next write to that key (which overwrites it) or a manual deletion.

---

## Method 4: Per-adapter Cache Key Structure

**When to use:** You want to understand why a particular query is or is not being served from cache, or you need to invalidate a specific entry.

Cache keys are constructed as:

```
{adapter_source_name}:{action}:{param1}:{param2}:...
```

Examples:
- `sec_edgar:10-K:AAPL:latest`
- `yahoo_finance:price:MSFT`
- `fred:series:GDP`

To delete a specific cache entry:

```bash
# List files in the cache to find the key
find .cache/zion/ -name "*.json" | head -20

# Delete a specific file (keys are hashed; inspect the cache manager for exact path format)
python - <<'EOF'
from zion_terminal.cache.cache_manager import CacheManager
cm = CacheManager()
key = cm._build_key("sec_edgar", {"action": "10-K", "ticker": "AAPL"})
print(cm._path_for_key(key))
EOF
```

Then delete the printed path.

---

## Disabling the Cache Entirely

For debugging or one-off operations, disable the cache:

```bash
ZION_CACHE_ENABLED=0 zion get-financials AAPL
```

Or programmatically:

```python
orc = Orchestrator(cache_enabled=False)
```

---

## Summary Table

| Scenario | Method | Command |
|---|---|---|
| Force full re-fetch now | Manual deletion | `rm -rf .cache/zion/` |
| Output format changed | Schema version bump | Edit `CACHE_SCHEMA_VERSION` + delete |
| Data goes stale over time | TTL configuration | Set `ZION_CACHE_TTL` |
| Debug one query | Disable cache | `ZION_CACHE_ENABLED=0 zion ...` |
| Single adapter stale | Per-adapter deletion | `rm -rf .cache/zion/<source_name>/` |

---

## Reference

- Cache manager: `src/zion_terminal/cache/cache_manager.py`
- ADR: `docs/decisions/002-cache-versioning.md`
