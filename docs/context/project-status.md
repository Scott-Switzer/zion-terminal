# Project Status — v0.3.1

## What Works
- Stock quotes, historical data, financials via Yahoo Finance
- SEC filings, company facts, filing→markdown via SEC EDGAR
- FRED macroeconomic data (GDP, CPI, unemployment, etc.)
- Natural-language intent parsing (rule-based, LLM optional)
- Data validation with strict mode (all CLI commands, including synthesis)
- Synthetic company generation
- File-based caching with TTL and schema versioning
- Filing conversion pipeline with section segmentation
  - Handles standard `<h1>`–`<h4>` headings
  - Promotes SEC Item headers in `<div>`, `<p>`, `<b>` tags to markdown headings
  - Segmenter falls back to plain-text patterns when no headings found
- CLI with all features exposed
- Converter metadata correctly reports `dom` vs `regex` engine
- LLM-assisted parsing flag surfaced in response metadata

## What's Experimental
- Filing→markdown conversion (improved for real filings but edge cases remain)
- XBRL verification via Arelle (optional, API-aligned as of v0.3.1)
- LLM-assisted intent parsing (tagged with `llm_assisted` in response metadata)

## What's Missing
- Real-time streaming data
- Multi-source correlation / cross-validation
- Historical accuracy benchmarks against authoritative sources (Bloomberg ground truth)
- CI/CD pipeline
- Type checking (mypy) enforcement
- Rate limiting for the CLI itself
- iXBRL inline parsing

## Known Limitations
- Yahoo Finance is an unofficial API — may break without notice
- SEC EDGAR rate limit: ~10 req/sec (enforced by adapter)
- FRED requires a free API key
- Filing markdown conversion works best on standard 10-K/10-Q formats
- Segmenter relies on "Item N" text patterns — filings with non-standard numbering may not segment

## Test Coverage
- 250+ unit tests (up from 218 in v0.3.0)
- 66 intent parser benchmark queries (up from 20)
- 3 HTML filing fixtures covering standard headings, div/p/b headers, and multi-table filings
- Positive-path XBRL wrapper tests (mocked, since Arelle is optional)

## Version History
| Version | Tests | Key Changes |
|---------|-------|-------------|
| v0.1.0 | 74 | Initial implementation from README spec |
| v0.2.0 | 155 | Surgical refactor: honest CLI, modular agents |
| v0.3.0 | 218 | Deep refactor: pipeline, validation, strict mode |
| v0.3.1 | 250+ | Follow-up patch: 8 bug fixes, expanded benchmarks |
