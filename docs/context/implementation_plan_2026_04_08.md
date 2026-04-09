# Master Implementation Plan — 2026-04-08

## Issue Priority Order

### P0 — Critical (Architecture Correctness)

1. **NL financials routing → SEC** (intent_parser.py)
   - Root cause: `_build_equity_task` hardcodes `yahoo_finance` for financials intent
   - Fix: Route financials/filings/company_facts/filing_markdown to `sec_edgar`
   - Test: Hard test that NL "AAPL financials" produces sec_edgar task

2. **SEC financials honors statement_type and quarterly** (sec_edgar.py)
   - Root cause: `_fetch_financials` ignores params, always fetches all 10-K statements
   - Fix: Filter by statement_type, use 10-Q for quarterly=True
   - Test: Mock SEC API, verify only requested statement returned

3. **Synthesis determinism uses stable hash** (synthesis/agent.py)
   - Root cause: `hash()` is PYTHONHASHSEED-dependent
   - Fix: Use `hashlib.sha256` for stable seed derivation
   - Test: Same query → same output across PYTHONHASHSEED values

### P1 — Important (Feature Completeness)

4. **Wire FilingVerifier into FilingPipeline** (filing_pipeline.py, verification.py)
   - Root cause: Stage 3 is placeholder
   - Fix: Call FilingVerifier.verify() with structural checks at minimum
   - Arelle integration degrades gracefully when unavailable

5. **Company facts enrichment** (sec_edgar.py)
   - Fix: Group by namespace/category, add pagination, filtering, richer output
   - Add CSV and markdown table formatters

6. **Benchmark runner produces real results** (scripts/run_benchmarks.py)
   - Fix: Actually run converter on fixtures, parser on corpus, count real tests
   - Regenerate latest.json and latest.md from real data

### P2 — Quality (Validation & Docs)

7. **Validation depth improvements** (validation/agent.py)
   - Add scale mismatch detection, sign check, cross-statement consistency
   - Wire reconciler into validation pipeline for SEC data

8. **README rewrite** to match actual code
9. **ADR/runbook/schema doc updates**
10. **Dead code cleanup**

## File-Level Change Map

| File | Changes |
|------|---------|
| `orchestrator/intent_parser.py` | Fix `_build_equity_task` to route financials to SEC |
| `agents/retrieval/adapters/sec_edgar.py` | Honor statement_type, quarterly params |
| `agents/synthesis/agent.py` | Replace hash() with hashlib.sha256 |
| `pipeline/filing_pipeline.py` | Wire FilingVerifier into process() |
| `pipeline/verification.py` | Enhance structural checks, mark Arelle status |
| `agents/retrieval/adapters/sec_edgar.py` | Enrich company_facts with grouping/filtering |
| `agents/validation/agent.py` | Add scale/sign checks, cross-statement checks |
| `outputs/formatter.py` | Enhance company_facts markdown/CSV output |
| `scripts/run_benchmarks.py` | Produce real benchmark data |
| `docs/benchmarks/latest.json` | Regenerate from real runner |
| `docs/benchmarks/latest.md` | Regenerate from real runner |
| `README.md` | Full rewrite matching live code |
| `tests/unit/test_*.py` | Add regression tests for each fix |
