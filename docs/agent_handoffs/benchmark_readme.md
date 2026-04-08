# Benchmark README for Future Agents

## Running Benchmarks

```bash
# All benchmarks (performance + accuracy)
pytest tests/benchmarks/ -v

# Performance only
pytest tests/benchmarks/ --benchmark-only

# Accuracy only (no perf measurement)
pytest tests/benchmarks/ -k "Accuracy or accuracy" -v

# Filing pipeline (live path)
pytest tests/benchmarks/ -k "Pipeline" -v
```

## What Benchmarks Measure

### Parser Benchmarks (`test_parser_benchmark.py`)
- **Performance**: queries/second for single and corpus parsing
- **Accuracy**: intent classification, ticker extraction, macro series detection
- **Corpus**: 66 queries in `tests/fixtures/sample_queries.json`

### Converter Benchmarks (`test_converter_benchmark.py`)
- **Performance**: filings/second for converter, segmenter, and full pipeline
- **Accuracy**: structure preservation (headers, tables, bold, lists)
- **Fixtures**: 4 HTML fixtures + 1 TOC-heavy fixture

## What Benchmarks Do NOT Measure
- Live API latency
- Real SEC filing accuracy (only synthetic fixtures)
- Memory usage
- Arelle validation performance (Arelle not installed in test env)

## How to Improve Benchmarks
1. Add real SEC filing fragments as fixtures
2. Create expected-output JSON files for each fixture
3. Measure section detection recall/precision
4. Add XBRL fact extraction accuracy benchmarks
5. Track performance trends across versions

## Interpreting Results
- Parser OPS >5K: acceptable
- Converter OPS >500: acceptable for small filings
- Pipeline OPS >400: acceptable (includes segmentation)
- Corpus accuracy: target 100% on curated corpus
