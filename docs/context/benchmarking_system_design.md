# Benchmarking System Design

This document describes the overall design of the Zion Terminal benchmark infrastructure: what it measures, how it is structured, and where the components live.

---

## Goals

The benchmark system serves three purposes:

1. **Correctness tracking** — verify that the filing pipeline extracts the right content (sections, numbers, tables) from HTML filings.
2. **Regression detection** — catch unintended degradation in parser accuracy, section recovery, or numeric fidelity across commits.
3. **Capability documentation** — produce human-readable reports (`docs/benchmarks/latest.md`) that communicate the system's current state to future agents and maintainers.

---

## Components

### 1. Fixture Corpus

Static HTML files in `tests/fixtures/` represent a range of real-world filing shapes:

- **Simple filings** — minimal structure, few sections, easy to parse.
- **Large filings** — full-size 10-Ks, stress-tests for conversion speed.
- **Div-header filings** — headings expressed as styled `<div>` elements rather than `<h1>`–`<h6>`.
- **TOC-heavy filings** — dense Item listings at the top of the document; tests TOC discrimination heuristic.
- **Wrapper filings** — documents that reference an exhibit rather than containing substantive content.
- **Multi-form** — 10-Q and 8-K fixtures to verify form-specific section expectations.

### 2. Scoring Metrics

The benchmark runner evaluates four primary metrics:

| Metric | Weight | Description |
|---|---|---|
| Section recovery | 35% | Fraction of expected sections successfully extracted. |
| Numeric fidelity | 45% | Accuracy of numeric value extraction from markdown tables vs. ground truth. |
| Table fidelity | 20% | Fraction of HTML tables correctly converted to markdown table syntax. |
| Parser accuracy | — | NL intent parser: fraction of corpus queries correctly classified (not part of composite score). |

The composite score is: `0.35 × section_recovery + 0.45 × numeric_fidelity + 0.20 × table_fidelity`

See `docs/decisions/ADR-0016-scoring-and-regression-policy.md` for the rationale behind these weights.

### 3. XBRL Reconciliation

When Arelle is installed, the benchmark includes XBRL reconciliation:

- `verification/fact_mapping.py` — maps raw Arelle facts to a canonical schema.
- `verification/markdown_extractor.py` — extracts numeric values from markdown tables.
- `verification/reconciler.py` — compares extracted facts against markdown values and reports match status.

Match statuses: `matched`, `scale_mismatch`, `sign_mismatch`, `label_mismatch`, `missing`.

When Arelle is not installed, XBRL reconciliation is skipped and the `xbrl_match_rate` metric is marked `"unavailable"`. The composite score is computed from the remaining three metrics only.

See `docs/context/xbrl_reconciliation_design.md` for the full reconciliation design.

### 4. Benchmark Runner

`scripts/run_benchmarks.py` orchestrates the full benchmark:

1. Load each HTML fixture.
2. Run `FilingPipeline.process_html()`.
3. Score section recovery against `expected_sections.json`.
4. Extract numeric values via `markdown_extractor.py`.
5. Compare against XBRL ground truth (if Arelle available).
6. Run the NL parser corpus.
7. Write results to `docs/benchmarks/latest.json` and `docs/benchmarks/latest.md`.

### 5. Regression Detection

The runner compares `latest.json` against the previous baseline (`docs/benchmarks/baseline.json`) and flags any metric that drops beyond the thresholds defined in `docs/benchmarks/regression_policy.md`.

---

## Module Map

```
src/zion_terminal/
├── pipeline/
│   └── filing_pipeline.py        # Core pipeline being benchmarked
├── verification/
│   ├── fact_mapping.py           # XBRL → canonical fact schema
│   ├── markdown_extractor.py     # Numeric extraction from markdown
│   └── reconciler.py             # Fact vs. markdown comparison
tests/
├── fixtures/                     # Static HTML fixtures
│   ├── *.html
│   └── *_expected_sections.json
├── test_converter_benchmark.py   # Converter-level benchmarks
└── test_pipeline.py              # Full pipeline benchmarks
scripts/
└── run_benchmarks.py             # Benchmark runner
docs/benchmarks/
├── latest.md                     # Human-readable report
├── latest.json                   # Machine-readable results
├── baseline.json                 # Previous run baseline (for regression)
├── methodology.md                # Fixture corpus and scoring methodology
├── regression_policy.md          # Thresholds and response procedures
└── known_gaps.md                 # Known limitations and missing coverage
```

---

## Current State (v0.4.3)

- Section recovery: implemented and tested.
- Parser accuracy: implemented (110+ query corpus).
- Numeric fidelity: `markdown_extractor.py` implemented; end-to-end integration with benchmark runner pending.
- Table fidelity: not yet implemented.
- XBRL reconciliation: modules exist (`fact_mapping.py`, `reconciler.py`); Arelle not available in CI.

---

## Reference

- ADR: `docs/decisions/ADR-0016-scoring-and-regression-policy.md`
- ADR: `docs/decisions/ADR-0017-xbrl-ground-truth-reconciliation.md`
- Context: `docs/context/xbrl_reconciliation_design.md`
- Context: `docs/context/markdown_extraction_strategy.md`
