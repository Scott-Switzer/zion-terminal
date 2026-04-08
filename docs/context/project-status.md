# Project Status — v0.4.0

## What Works
- Stock quotes, historical data via Yahoo Finance (market data primary source)
- SEC filings, company facts, filing→markdown via SEC EDGAR (company data primary source)
- Financial statements via SEC EDGAR (primary) or Yahoo Finance (fallback)
- FRED macroeconomic data (GDP, CPI, unemployment, etc.)
- Natural-language intent parsing (rule-based, LLM optional)
- Data validation with strict mode (all CLI commands)
- Synthetic company generation (deterministic, seeded RNG)
- File-based caching with TTL and schema versioning
- Unified filing pipeline: converter → segmenter → verification
  - DOM-based HTML→markdown conversion
  - SEC Item header promotion from div/p/b tags
  - Segmenter fallback to plain-text patterns
  - Structural verification built into pipeline
  - XBRL/Arelle verification (optional dependency)
  - Cross-source verification hooks
- Source role model codifying SEC-first hierarchy
- CLI with all features exposed, including `--source` for financials

## What's Experimental
- Filing→markdown conversion (improved for real filings, edge cases remain)
- XBRL verification via Arelle (optional, API-aligned)
- LLM-assisted intent parsing (tagged in response metadata)
- Cross-source reconciliation (hooks exist, automation pending)

## What's Missing
- Real-time streaming data
- Automated cross-source reconciliation
- Historical accuracy benchmarks against authoritative sources
- CI/CD pipeline
- Type checking (mypy) enforcement
- iXBRL inline parsing
- Wrapper filing detection and resolution

## Known Limitations
- Yahoo Finance is an unofficial API — may break without notice
- SEC EDGAR rate limit: ~10 req/sec (enforced by adapter)
- FRED requires a free API key
- Filing markdown conversion works best on standard 10-K/10-Q formats
- Segmenter relies on "Item N" text patterns

## Test Coverage
- 277+ tests
- 66 intent parser benchmark queries
- 4 HTML filing fixtures (standard, div headers, table TOC, wrapper)
- Positive-path XBRL wrapper tests (mocked)
- Source role tests, filing pipeline tests, verification tests, determinism tests

## Version History
| Version | Tests | Key Changes |
|---------|-------|-------------|
| v0.1.0 | 74 | Initial implementation from README spec |
| v0.2.0 | 155 | Surgical refactor: honest CLI, modular agents |
| v0.3.0 | 218 | Deep refactor: pipeline, validation, strict mode |
| v0.3.1 | 249 | Follow-up patch: 8 bug fixes, expanded benchmarks |
| v0.4.0 | 277+ | Architecture refactor: SEC-first, unified pipeline, deterministic synthesis |
