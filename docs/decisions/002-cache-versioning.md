# ADR-002: Cache Key Schema Versioning

## Status
Accepted (v0.3.0)

## Context
When adapter output formats change (new fields, renamed fields, restructured
data), cached entries from the old format can cause deserialization errors or
incorrect results. Users would need to manually clear their cache.

Additionally, the FRED adapter was not including `start_date` and `end_date`
in its cache key, causing different date ranges to return the same stale result.

## Decision
1. Add `CACHE_SCHEMA_VERSION` constant to `cache_manager.py` (starts at 1)
2. Include `_v` in every cache key hash
3. Bumping the version auto-invalidates all old cache entries
4. Fix FRED adapter to include all query-relevant params in cache key

## Consequences
- Schema changes don't require manual cache clearing
- Slight increase in cache misses after version bumps (acceptable)
- All adapters must pass complete params to `_cache_get`/`_cache_set`
