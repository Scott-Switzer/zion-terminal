# Benchmarks

## Running Benchmarks

```bash
# All benchmarks
pytest tests/benchmarks/ -v

# Performance benchmarks only
pytest tests/benchmarks/ --benchmark-only

# Accuracy benchmarks only (no perf measurement)
pytest tests/benchmarks/ -k "Accuracy" -v
```

## Benchmark Files

| File | What It Tests |
|---|---|
| `test_parser_benchmark.py` | Parser performance and corpus accuracy |
| `test_converter_benchmark.py` | Converter, segmenter, and pipeline performance + accuracy |

## Methodology
See `methodology.md` for details on how benchmarks are structured and what the numbers mean.

## Known Gaps
See `known_gaps.md` for areas where benchmarks are insufficient.
