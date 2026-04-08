# Test Fixtures

Static test data for deterministic, offline testing.

## Files

- `sample_queries.json` — Intent parser test corpus: queries + expected intents/tickers
- `sample_html_filing.html` — Minimal 10-K HTML for converter/segmenter tests
- `sample_fred_response.json` — FRED API mock response
- `sample_yf_quote.json` — Yahoo Finance quote mock response

## Usage

All fixtures are loaded in tests via `pathlib.Path(__file__).parent / "fixtures"`.
Do not commit real API responses containing PII or API keys.
