# Full Codebase Audit — 2026-04-08

## Repo State at Audit Time
- Version: 0.4.3
- Tests: 316 passing (9.48s)
- Python: 3.12.8

---

## CRITICAL FINDINGS

### A. NL Financials Query Routes to Yahoo, NOT SEC (CONFIRMED BUG)
**File:** `src/zion_terminal/orchestrator/intent_parser.py`, line in `_build_equity_task()`
**Evidence:** When intent="financials", `_build_equity_task()` returns `{"source": "yahoo_finance", ...}`.
This means `zion query "Show me AAPL financials"` goes to Yahoo, contradicting the SEC-first claim.

The direct `get_financials()` method on Orchestrator correctly routes to SEC.
The NL `query()` path does NOT — it uses IntentParser._build_equity_task which hardcodes `yahoo_finance`.

**Severity:** HIGH — the primary user-facing NL path ignores SEC-first architecture.

### B. SEC Financials Ignores statement_type and quarterly Parameters (CONFIRMED BUG)
**File:** `src/zion_terminal/agents/retrieval/adapters/sec_edgar.py`, `_fetch_financials()`
**Evidence:** The method always calls `company.get_filings(form="10-K")` regardless of:
- `statement_type` parameter (always fetches all three statements)
- `quarterly` parameter (always fetches 10-K, never 10-Q)

The params dict receives `statement_type` and `quarterly` but they are never read.

**Severity:** HIGH — user parameters are silently ignored.

### C. Synthesis Determinism Uses `hash()` (CONFIRMED BUG)
**File:** `src/zion_terminal/agents/synthesis/agent.py`, line ~44
**Evidence:** `self._rng = random.Random(hash(query) & 0xFFFFFFFF)` 
Python's `hash()` is NOT stable across processes (PYTHONHASHSEED is random by default since 3.3). 
The test `test_same_query_reproducible_without_explicit_seed` only passes within a single process.
Cross-process determinism is false.

**Severity:** HIGH — documented determinism guarantee is unreliable.

### D. Filing Pipeline Verification Is a Placeholder (CONFIRMED)
**File:** `src/zion_terminal/pipeline/filing_pipeline.py`, line ~100
**Evidence:** Stage 3 always sets `verification = {"status": "pending", "xbrl_available": False, ...}`
The FilingVerifier class exists but is never called from FilingPipeline.process().

**Severity:** MEDIUM — verification is structurally present but not wired in.

### E. Benchmark Artifacts Are Fabricated (CONFIRMED)
**File:** `docs/benchmarks/latest.json`
**Evidence:**
- References fixture files that don't exist (e.g., `sample_10k_simple.html`, `sample_10k_large.html`, `sample_10q_simple.html`, `sample_8k_simple.html`)
- Only these fixtures exist: `sample_html_filing.html`, `sample_html_filing_div_headers.html`, `sample_html_filing_table_toc.html`, `sample_html_filing_wrapper.html`, `toc_heavy_filing.html`
- Claims 302 tests; actual count is 316
- References `markdownify` as converter; code uses BeautifulSoup DOM converter
- Claims 7 fixtures; only 5 HTML fixtures exist
- Regression comparison to "v0.4.2 / commit abc1234" — no evidence this commit or version existed

**Severity:** HIGH — benchmark artifacts are misleading.

### F. Company Facts Is Too Thin (CONFIRMED)
**File:** `src/zion_terminal/agents/retrieval/adapters/sec_edgar.py`, `_fetch_company_facts()`
**Evidence:** Returns only `df.head(20)` as sample_facts, or a truncated string summary.
No filtering, grouping, pagination, namespace selection, or structured output.

**Severity:** MEDIUM — feature exists but is not useful for serious analysis.

### G. README Doesn't Match Live Repo (CONFIRMED)
**Evidence:**
- README project structure doesn't match (no `config/` dir in root, no `outputs/` in the structure shown)
- README claims `src/` structure that doesn't include `pipeline/`, `verification/`, `providers/`
- README roadmap checkmarks show nothing completed in Phase 1
- README git clone URL is `github.com/zion-terminal/zion-terminal` but actual repo is `Scott-Switzer/zion-terminal`
- README says "Full setup instructions will be added" but setup actually works
- No mention of CLI commands that exist

**Severity:** HIGH — README is outdated vs actual codebase.

---

## ADDITIONAL FINDINGS

### H. Cross-Source Verification is Stub
**File:** `src/zion_terminal/pipeline/verification.py`, `_check_cross_source()`
Only records that Yahoo data was available. Does no actual comparison.

### I. Validation Doesn't Check Scale or Sign Mismatches on Retrieval
**File:** `src/zion_terminal/agents/validation/agent.py`
Reconciler and MarkdownExtractor exist in `verification/` but are not used by ValidationAgent.

### J. No Validation of SEC vs Yahoo Data Consistency
The source_roles.py defines roles but nothing enforces cross-source checks.

### K. TOC Filter Uses 200-Line Threshold
Short filings under 200 lines skip TOC filtering, which is fine, but the threshold isn't configurable.

### L. Benchmark Runner Doesn't Actually Run Converter/Parser Benchmarks
**File:** `scripts/run_benchmarks.py`
Only counts tests and fixtures. Doesn't run converter timing, parser accuracy, or fixture processing.

### M. The `_ITEM_HEADER_RE` in converter.py is slightly different from segmenter patterns
Converter promotes Item headers to `## `, segmenter looks for them. Compatible but subtle coupling.

### N. Unused `providers/__init__.py` has no exports
Minor — no impact.

---

## VERIFICATION OF README CLAIMS

| Claim | Status |
|-------|--------|
| SEC EDGAR is primary for financials | PARTIAL — Direct API yes, NL query NO |
| Filing→markdown pipeline | WORKS |
| Company facts | EXISTS but too thin |
| Validation | EXISTS but shallow |
| Strict mode | WORKS |
| Deterministic synthesis | BROKEN across processes |
| CLI routes through orchestrator | WORKS |
| All output formats (md/json/csv) | WORKS |

---

## TEST COVERAGE GAPS

1. No test verifying NL financials query routes to SEC (there is one, but it mocks at wrong level)
2. No cross-process determinism test
3. No test verifying `statement_type` and `quarterly` params are honored in SEC adapter
4. No test that benchmark runner produces accurate output
5. No test for FilingVerifier being wired into FilingPipeline
