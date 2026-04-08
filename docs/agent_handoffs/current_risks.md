# Current Risks

## High Risk

### 1. SEC Financials Path Untested Against Live API
The SEC financials adapter (`_fetch_financials`) is only tested via mocks. Real edgartools responses may differ from mock shapes. Risk: schema mismatch in production.

**Mitigation**: Run integration test against live SEC API before any production use.

### 2. Fixture Corpus is Synthetic
All 5 HTML fixtures are hand-written, <3KB each. Real SEC filings are 100KB-10MB with complex nested tables, embedded images, and XBRL tags. Pipeline behavior on real filings is not validated.

**Mitigation**: Add real filing fragments. Track accuracy metrics.

### 3. Yahoo Finance API Instability
yfinance is an unofficial API. It can break without notice. The project depends on it for market data.

**Mitigation**: Market data is convenience, not critical path. Filing-based financial data comes from SEC.

## Medium Risk

### 4. TOC Discrimination is Heuristic
The TOC filter uses a simple density heuristic (3+ items in <30 lines in the first 20% of the document). Real TOCs may not match this pattern.

### 5. No CI/CD
All testing is local. No automated regression detection on push.

### 6. Arelle Not Tested with Real XBRL
XBRL validation is tested via mocks only. Real Arelle behavior with SEC filing XBRL is not validated.

## Low Risk

### 7. Parser Corpus Size
66 queries is reasonable for a PoC but not sufficient for production NL understanding.

### 8. Cache Invalidation
No tests for stale cache behavior after schema version bumps.
