# Benchmark Known Gaps

## Missing Coverage

1. **Real SEC filing benchmarks**: All fixtures are synthetic <3KB HTML. Real 10-Ks are 100KB-10MB with complex nested tables, embedded images, and thousands of XBRL tags.

2. **iXBRL conversion benchmarks**: No iXBRL fixtures exist. Real SEC filings increasingly use inline XBRL.

3. **Cross-source reconciliation benchmarks**: No benchmarks for comparing SEC-derived values against Yahoo Finance data.

4. **XBRL validation benchmarks**: Arelle is not installed in the test environment. XBRL validation is only tested via mocks.

5. **Large corpus accuracy**: The 66-query parser corpus is hand-curated. It does not cover the full range of natural-language financial queries.

6. **Edge case coverage**: Only 1 wrapper filing fixture, 1 TOC-heavy fixture. Real SEC filings have many more edge cases.

7. **Memory benchmarks**: No memory usage tracking for large filing processing.

## How to Improve

- Add 5-10 real (anonymized) SEC filing HTML fragments as fixtures
- Add iXBRL fixture from a real inline filing
- Install Arelle in CI and run real XBRL validation benchmarks
- Expand query corpus to 200+ queries with adversarial cases
- Add memory profiling for large filing processing
