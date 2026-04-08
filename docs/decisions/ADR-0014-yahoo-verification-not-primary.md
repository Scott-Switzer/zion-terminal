# ADR-0014: Yahoo Finance — Verification, Not Primary

## Status
Accepted

## Context
The repo treated Yahoo Finance as a default primary source for financial statements. Per the project's mission as a Bloomberg alternative, SEC filings are the authoritative source.

## Decision
Yahoo Finance is repositioned:
- Primary for market data (quotes, history) — this is its strength
- Verification/cross-check for financial statements — compare against SEC-derived data
- Convenience for company info — quick lookup, not authoritative

## Consequences
- `get_financials()` defaults to SEC EDGAR
- `--source yahoo` flag allows explicit Yahoo fallback
- Source roles documented in `models/source_roles.py`
- No Yahoo data is presented as authoritative for financial statements
