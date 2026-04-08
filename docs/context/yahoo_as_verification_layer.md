# Yahoo Finance as Verification Layer

## Role
Yahoo Finance is NOT the primary source of financial truth. It serves three roles:

1. **Primary for market data** — real-time quotes, historical prices, market cap
2. **Verification for financials** — cross-check against SEC-derived statements
3. **Convenience for company info** — quick lookup of sector, industry, description

## Where Yahoo IS Used
- `get_quote()` — market data is Yahoo's primary domain
- `get_history()` — historical price data
- `get_info()` — company profile convenience
- Cross-source verification — comparing SEC-derived values against Yahoo

## Where Yahoo IS NOT the Source of Truth
- Financial statements — SEC EDGAR is primary
- Filing content — SEC EDGAR is the only source
- XBRL company facts — SEC EDGAR is the only source
- Regulatory filings — SEC EDGAR is the only source

## CLI Behavior
- `zion financials AAPL` → defaults to SEC EDGAR
- `zion financials AAPL --source yahoo` → explicitly requests Yahoo
- `zion quote AAPL` → Yahoo (market data)
- `zion filings AAPL` → SEC (always)

## Implementation
The `SOURCE_ROLES` matrix in `models/source_roles.py` encodes these roles. The orchestrator's `get_financials()` method defaults to SEC and falls back to Yahoo when SEC is unavailable or explicitly requested.
