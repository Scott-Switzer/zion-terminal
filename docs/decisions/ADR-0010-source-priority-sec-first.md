# ADR-0010: Source Priority — SEC First

## Status
Accepted

## Context
The project aims to be a Bloomberg Terminal alternative. Bloomberg's financial data is derived from SEC filings. The repo previously defaulted to Yahoo Finance for financial statements, which is a secondary/derived source.

## Decision
SEC EDGAR is the primary source for company financial data. Yahoo Finance is repositioned as a verification/cross-check/fallback layer.

The source hierarchy is:
- Financial statements: SEC (primary), Yahoo (verification)
- Filing content: SEC (only source)
- Company facts: SEC/XBRL (primary), Arelle (verification)
- Market data: Yahoo (primary)
- Macro data: FRED (primary)

## Consequences
- `get_financials()` defaults to SEC EDGAR
- Yahoo Finance is available via explicit `--source yahoo` flag
- The `SOURCE_ROLES` matrix in `models/source_roles.py` is the single source of truth for source hierarchy
- Future cross-source reconciliation will compare SEC-derived data against Yahoo
