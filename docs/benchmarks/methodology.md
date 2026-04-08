# Benchmark Methodology

## Performance Benchmarks

All performance benchmarks use `pytest-benchmark` with default settings:
- Automatic round detection (minimum 5 rounds)
- Warmup enabled
- Statistical aggregation (min, max, mean, stddev, median, IQR)

### What is measured
- **Parser**: Time to parse a single query or the full 66-query corpus
- **Converter**: Time to convert a small (~2KB) or large (~100KB) HTML fixture
- **Segmenter**: Time to segment a converted markdown document
- **Pipeline**: Time to run the full FilingPipeline.process() on a fixture

### What is NOT measured
- Live API latency (network-dependent, not benchmarked)
- Real SEC filing processing (filings are 100KB-10MB, not represented by fixtures)
- Memory usage

## Accuracy Benchmarks

### Parser Corpus
- 66 queries with expected intents, tickers, and macro series
- 100% accuracy required — any failure fails the test
- Ticker extraction allows 80% threshold (some tickers are genuinely ambiguous)

### Converter
- Structure preservation: headers, tables, bold, lists
- Script/style stripping
- Metadata correctness

### Segmenter
- Section detection: ≥3 sections for standard 10-K fixture
- Section lookup by item number
- Summary generation

## Fixture Corpus
| Fixture | Size | Purpose |
|---|---|---|
| sample_html_filing.html | ~2KB | Standard 10-K with h1-h4 headings |
| sample_html_filing_div_headers.html | ~1.4KB | Real-world div/p/b Item headers |
| sample_html_filing_table_toc.html | ~3KB | Filing with TOC table and 9+ sections |
| sample_html_filing_wrapper.html | ~1KB | Minimal wrapper filing |

All fixtures are synthetic. They represent common filing patterns but are NOT real SEC filings.
