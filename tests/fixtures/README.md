# Test Fixtures

Shared test data for unit tests and benchmarks.

## Files

| File | Description |
|---|---|
| `sample_queries.json` | 66 query/intent/ticker/macro test cases for parser benchmarks |
| `sample_html_filing.html` | Toy SEC 10-K HTML with headings, tables, lists, bold/italic |
| `sample_html_filing_div_headers.html` | SEC filing HTML where Item headers are in div/p/b tags (realistic edge case) |
| `sample_html_filing_table_toc.html` | Larger SEC 10-K with table of contents, multiple tables, 10 sections |

## Adding Fixtures

- Query fixtures: add to `sample_queries.json` with `query`, `expected_intent`, and optionally `expected_tickers`, `expected_macro_series`, `note`.
- HTML fixtures: use realistic SEC filing structure. Name as `sample_html_filing_*.html`.
