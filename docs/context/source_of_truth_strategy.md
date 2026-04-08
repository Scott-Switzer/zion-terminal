# Source of Truth Strategy

## Principle
SEC EDGAR is the authoritative source for company financial data. Yahoo Finance provides market convenience data and serves as a cross-check layer.

## Source Hierarchy

| Data Domain | Primary Source | Verification | Fallback |
|---|---|---|---|
| Financial Statements | SEC EDGAR (10-K/10-Q filings) | Yahoo Finance | — |
| Filing Content | SEC EDGAR | — | — |
| Company Facts (XBRL) | SEC EDGAR | Arelle | — |
| Market Data (quotes, history) | Yahoo Finance | — | — |
| Macro Data | FRED | — | — |
| Company Info | SEC EDGAR (filings) | Yahoo Finance | — |

## Why SEC First

1. SEC filings are the legal record of a company's financial position.
2. XBRL-tagged data provides machine-readable structured facts.
3. Yahoo Finance data is derived from SEC filings (with a lag) and may contain estimation.
4. Bloomberg validation (internal only, per project rules) confirms SEC as ground truth.

## Implementation

The source role model is defined in `src/zion_terminal/models/source_roles.py`. The `SOURCE_ROLES` dict maps each source to its role per data domain. The orchestrator uses this to route requests.

### Current State (v0.4.0)
- `get_financials()` defaults to SEC EDGAR via edgartools
- `get_quote()` and `get_history()` use Yahoo Finance (market data is its primary domain)
- `get_filing_markdown()` and `get_company_facts()` are SEC-only
- `get_info()` uses Yahoo Finance as a convenience source
- Verification hooks exist but cross-source reconciliation is not yet automated

### Future
- Automated reconciliation: compare SEC-derived financials against Yahoo Finance
- Bloomberg ground truth validation (internal, never published)
- Multi-filing historical comparison
