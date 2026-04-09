# ADR-0019: Direct SEC Client Supplementing edgartools

**Date:** 2026-04-08  
**Status:** Accepted

## Context

The SEC adapter relied entirely on edgartools for filing discovery, ticker→CIK resolution, and company facts retrieval. This created several reliability problems:

1. **No historical filtering** — edgartools does not support filtering filings by year, quarter, or date range
2. **Ticker resolution brittleness** — edgartools' `Company(ticker)` can fail for class-share tickers (BRK-B), delisted companies, or renamed entities
3. **Single point of failure** — if edgartools fails, the entire SEC path fails

## Decision

Supplement edgartools with a direct SEC client (`src/zion_terminal/sec/client.py`) that uses SEC's free JSON APIs:

- `sec.gov/files/company_tickers.json` for ticker→CIK resolution
- `data.sec.gov/submissions/CIK{cik}.json` for filing discovery
- `data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` for XBRL facts

**Keep edgartools for:** financial statement parsing (`filing.obj().financials`) where it is strong.

**Use direct SEC client for:**
- Ticker→CIK resolution (as fallback when edgartools fails)
- Filing discovery with year/quarter/date filtering
- Filing metadata retrieval
- Company facts (as fallback)

## Architecture

```
CLI/Orchestrator
    ↓
SEC Adapter
    ├── Direct SEC Client (filing discovery, CIK resolution, date filtering)
    └── edgartools (financial statement parsing, filing HTML extraction)
```

When year/quarter/date filters are specified, the adapter uses the direct SEC client to discover matching filings, then uses edgartools to parse their financial statements.

## Consequences

- Historical SEC retrieval now works (year, quarter, date range filtering)
- Ticker resolution is more robust (BRK.B, delisted companies)
- No new third-party dependencies (uses `requests`, already a dependency)
- SEC rate limits must be respected across both clients (shared rate limiter)
- Direct SEC client caches the ticker map in memory (loaded once per process)
