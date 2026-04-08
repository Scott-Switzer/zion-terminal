# Known Limitations

## Data Sources
- **Yahoo Finance** is an unofficial API — may break without notice
- **SEC EDGAR** rate limit: ~10 req/sec (enforced by adapter)
- **FRED** requires a free API key
- **Arelle** is an optional dependency; XBRL validation only works when installed

## Filing Pipeline
- Filing markdown conversion works best on standard 10-K/10-Q formats
- ~10% of filings use non-standard heading formats and may not segment
- Wrapper filings (referencing separate annual reports) are not auto-resolved
- TOC entries may occasionally be confused with real section headers
- iXBRL inline documents are not yet parsed natively

## Validation
- Cross-source reconciliation has hooks but no automated logic yet
- Historical filing comparison is not implemented
- XBRL validation is schema-level only, not semantic
- Bloomberg ground truth comparison is internal-only and not automated

## Parser
- NL query intent parser assigns source names that may not reflect SEC-first hierarchy
- The auto-router corrects this at the task level, but parser-level source awareness is not yet implemented
- Ticker extraction may miss uncommon tickers or misidentify common words

## Synthesis
- Deterministic only in no-LLM mode
- LLM-generated press releases are inherently non-deterministic
- Financial values are template-based, not industry-calibrated

## Infrastructure
- No CI/CD pipeline
- No mypy type checking enforcement
- No rate limiting for CLI itself
- Cache schema version not yet bumped for v0.4.x
