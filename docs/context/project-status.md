# Project Status — v0.3.0

## What Works
- Stock quotes, historical data, financials via Yahoo Finance
- SEC filings, company facts, filing→markdown via SEC EDGAR
- FRED macroeconomic data (GDP, CPI, unemployment, etc.)
- Natural-language intent parsing (rule-based, LLM optional)
- Data validation with strict mode
- Synthetic company generation
- File-based caching with TTL and schema versioning
- Filing conversion pipeline with section segmentation
- CLI with all features exposed

## What's Experimental
- Filing→markdown conversion (works but may miss edge cases in complex filings)
- XBRL verification via Arelle (optional dependency, thin wrapper)
- LLM-assisted intent parsing (tagged with `_llm_assisted`)

## What's Missing
- Real-time streaming data
- Multi-source correlation / cross-validation
- Historical accuracy benchmarks against authoritative sources
- CI/CD pipeline
- Type checking (mypy) enforcement
- Rate limiting for the CLI itself

## Known Limitations
- Yahoo Finance is an unofficial API — may break without notice
- SEC EDGAR rate limit: ~10 req/sec (enforced by adapter)
- FRED requires a free API key
- Filing markdown conversion works best on standard 10-K/10-Q formats
- No support for XBRL inline (iXBRL) parsing yet
