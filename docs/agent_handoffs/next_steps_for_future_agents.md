# Next Steps for Future Agents

## Immediate Priorities

### 1. Automated Cross-Source Reconciliation
Hooks exist in `pipeline/verification.py` (`_check_cross_source`). Need to:
- Extract key numeric values from SEC-derived markdown (revenue, total assets, net income)
- Compare against Yahoo Finance data for the same period
- Report material discrepancies (>5% threshold)
- Label all comparisons as "heuristic"

### 2. Real SEC Filing Benchmarks
Current fixtures are synthetic <3KB HTML. Need:
- 5-10 real (anonymized) SEC filing fragments as fixtures
- Run pipeline on real Apple/Microsoft 10-Ks
- Measure section detection accuracy against hand-labeled expected output
- Create `tests/fixtures/expected_sections_*.json` for each real fixture

### 3. Parser SEC-Awareness
The NL intent parser assigns `source="yahoo_finance"` to financial queries. The auto-router corrects this, but the parser should natively prefer SEC for financial statement queries.

### 4. iXBRL Support
Arelle supports inline XBRL. Wire it into the pipeline:
- Detect iXBRL documents (look for `ix:` namespace in HTML)
- Run Arelle's iXBRL validation
- Extract inline facts alongside narrative text

### 5. Historical Filing Comparison
Compare current filing against prior year's filing for the same company:
- Flag material changes in key metrics
- Track section additions/removals
- Report period-over-period trends

## Medium-Term Goals

### 6. CI/CD Pipeline
Set up GitHub Actions for:
- Unit tests on every push
- Benchmark regression detection
- Doc-code consistency checks

### 7. Wrapper Filing Resolution
Detect wrapper filings and follow references to constituent documents.

### 8. Bloomberg Ground Truth Comparison
Internal-only: compare SEC-derived values against Bloomberg data for validation.

## Code Orientation for New Agents

### Key files to read first:
1. `src/zion_terminal/orchestrator/orchestrator.py` — entry point for all requests
2. `src/zion_terminal/pipeline/filing_pipeline.py` — the filing conversion pipeline
3. `src/zion_terminal/models/source_roles.py` — source hierarchy
4. `src/zion_terminal/agents/retrieval/adapters/sec_edgar.py` — SEC data adapter
5. `docs/architecture/overview.md` — system architecture

### Key tests to run:
```bash
pytest tests/unit/ -x -q          # All unit tests
pytest tests/benchmarks/ -v        # Benchmarks
pytest tests/unit/test_hardening.py -v  # Architecture invariant tests
```

### Regression tests to never break:
- `test_pipeline_is_the_only_converter_path`
- `test_financials_auto_routes_to_sec`
- `test_strict_failure_does_not_print_data`
- `test_same_seed_same_output`
