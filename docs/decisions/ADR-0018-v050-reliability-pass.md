# ADR-0018: v0.5.0 Reliability and Architecture Correction Pass

**Date:** 2026-04-08  
**Status:** Accepted  
**Context:** Full codebase audit revealed critical bugs and documentation drift.

## Changes Made

### Critical Fixes

1. **NL financials routing (intent_parser.py)**
   - Problem: `_build_equity_task()` routed financials intent to `yahoo_finance`, contradicting SEC-first architecture
   - Fix: Changed to route to `sec_edgar` for financials, filings, filing_markdown, company_facts
   - Impact: All NL financial queries now use SEC as primary source

2. **SEC financials parameters (sec_edgar.py)**
   - Problem: `_fetch_financials()` ignored `statement_type` and `quarterly` parameters. Always fetched all 10-K statements.
   - Fix: Added `_STMT_ATTR_MAP`, reads `quarterly` (10-Q vs 10-K), filters by `statement_type`
   - Impact: Users get exactly the statement they requested

3. **Synthesis determinism (synthesis/agent.py)**
   - Problem: Used `hash(query)` which varies across processes (PYTHONHASHSEED randomization since Python 3.3)
   - Fix: Replaced with `hashlib.sha256(query.encode()).digest()` → stable 8-byte int seed
   - Impact: Same query now produces identical output across process boundaries
   - Regression test: Subprocess test with different PYTHONHASHSEED values

4. **Filing pipeline verification (filing_pipeline.py)**
   - Problem: Stage 3 was a placeholder returning `{"status": "pending"}`
   - Fix: Wired `FilingVerifier.verify()` into pipeline. Runs structural checks, XBRL status, cross-source hooks
   - Impact: Verification is now live with real statuses

### Enrichment Fixes

5. **Company facts** — Added namespace filtering, concept filtering, pagination (offset/limit), namespace grouping summary
6. **Validation depth** — Added balance sheet identity check, scale plausibility check, sign plausibility check

### Benchmark Fixes

7. **Benchmark runner** — Complete rewrite. Now runs real converter/segmenter on actual fixtures, real parser accuracy on actual corpus. No fabricated data.
8. **Benchmark artifacts** — Regenerated from real measurements. Old artifacts referenced non-existent fixtures and wrong converter engine.

### Documentation Fixes

9. **README** — Full rewrite matching live code: correct git URL, correct project structure, all CLI commands, accurate source roles, honest known limitations
10. **Version bump** — 0.4.3 → 0.5.0 (breaking: NL routing changed, synthesis seeds changed)

## Alternatives Considered

- Could have left Yahoo as default for NL financials for backwards compatibility. Rejected: the SEC-first design was intentional and the bug was hiding it.
- Could have kept `hash()` with `PYTHONHASHSEED=0` documentation. Rejected: relying on environment configuration for correctness is fragile.

## Regression Protection

- 16 new regression tests in `test_regression_fixes.py`
- Cross-process determinism test (subprocess with varying PYTHONHASHSEED)
- Source inspection tests that verify code structure hasn't reverted
- Updated existing test from placeholder assertion to live verification assertion
