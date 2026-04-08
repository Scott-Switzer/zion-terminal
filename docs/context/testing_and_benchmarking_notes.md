# Testing and Benchmarking Notes

## Test Structure

| Directory | Purpose | Count |
|---|---|---|
| `tests/unit/` | Unit tests for all modules | ~250 |
| `tests/benchmarks/` | Performance and accuracy benchmarks | ~30 |
| `tests/integration/` | End-to-end tests (require live APIs) | ~5 |
| `tests/fixtures/` | HTML fixtures and query corpus | 5 files |

## What Tests Cover

### Unit Tests
- CLI command registration and argument passing
- Orchestrator public methods and strict mode
- Intent parser (66-query corpus, ticker extraction, macro detection)
- Filing pipeline (all 4 HTML fixtures)
- Verification module (structural, XBRL unavailable, cross-source hooks)
- Validation agent (retrieval checks, synthesis checks)
- Source role model
- Deterministic synthesis
- Dead code detection (no private converter in SEC adapter)
- Auto-routing (financials → SEC, quotes → Yahoo)
- Strict mode CLI behavior (exit codes, output suppression)

### Benchmark Tests
- Parser performance (single query, full corpus)
- Converter performance (small and large filings)
- Segmenter performance
- Filing pipeline performance (live path)
- Parser corpus accuracy (intent, tickers, macro series)
- Converter accuracy (structure preservation)
- Segmenter accuracy (section detection)

## What Tests Do NOT Cover
- Live API calls (SEC, Yahoo, FRED) — these are mocked in unit tests
- Arelle validation with real XBRL documents — mocked
- Cross-source reconciliation logic — hooks only
- Real-world filing edge cases beyond 4 fixtures
- Performance under load

## Benchmark Methodology
- Benchmarks use `pytest-benchmark` with default settings
- All benchmarks run offline against local fixtures
- Accuracy benchmarks compare parser/converter output against expected values in fixtures
- No live API benchmarks (would be flaky and rate-limited)

## What Benchmark Numbers Mean
- **Parser OPS**: queries parsed per second. Higher is better. Current: ~10K ops/sec for single queries.
- **Converter OPS**: filings converted per second. Current: ~600 ops/sec for small filings.
- **Corpus accuracy**: percentage of queries correctly classified. Target: 100% on the 66-query corpus.
- **Section detection**: number of sections found vs expected. Target: ≥3 for standard 10-K fixtures.

## What Benchmark Numbers Do NOT Mean
- Performance with real SEC filings (which are 10-100x larger than fixtures)
- Accuracy on the full universe of SEC filings
- Production throughput
