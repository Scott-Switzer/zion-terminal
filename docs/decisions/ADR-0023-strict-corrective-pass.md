# ADR-0023: Strict Corrective Pass (v0.7.1)

**Date:** 2026-04-08  
**Status:** Accepted

## Problems Found and Fixed

### 1. Period Matching Was Not Truly Strict
The previous code had a hidden fallback: when exact `(target_fy, target_fp)` match failed, it silently relaxed to year-only matching. This violated the "no hidden fallback" rule.

**Fix:** Removed the silent relaxation. If `target_fp` is specified, only exact `(fy, fp)` matches are accepted. If `target_fp` is None, year-only matching is used but explicitly surfaced via `period_match_mode`.

**New match modes:**
- `exact_period`: Both FY and FP matched from XBRL metadata
- `heuristic`: FY derived from filing date (less reliable)
- `year_only`: FY matched but FP unknown
- `no_filing_date`: No filing date available

**Downgrade rule:** `reconciled_pass` requires `exact_period`. If period is heuristic, status is capped at `reconciled_partial`.

### 2. Quarter Matching Was Missing
10-Q filings set `target_fp = None`, making quarter reconciliation impossible.

**Fix:** `_derive_fiscal_period()` now attempts to determine the exact quarter from company facts metadata (matching filed date). Falls back to filing-month heuristic with explicit mode tracking.

### 3. Fiscal Year Derivation Was Heuristic-Only
The `month > 6` rule fails for non-calendar fiscal years.

**Fix:** `_derive_fiscal_period()` now validates the candidate FY against company facts by checking for matching `(fy, fp)` entries. If found, mode is `exact_period`. If not found, mode is `heuristic` (honestly documented).

### 4. Benchmarks Did Not Prove Reconciliation
All fixtures showed `structural_only` because no company facts were passed.

**Fix:** The benchmark runner now passes `sample_company_facts.json` to reconciliation-eligible fixtures. Benchmarks now show:
- 2 fixtures: `reconciled_pass`, `exact_period`, 8 facts matched
- 5 fixtures: `structural_only`, no company facts

### 5. CleanedDocument Was Unused Scaffolding
The model existed but was never called from any live path.

**Fix:** `FilingPipelineResult.to_cleaned_document()` is now a real method. `DocumentStore` is tested with roundtrip persistence.

### 6. JSON Output Hid Reconciliation Truth
Only the markdown formatter showed reconciliation data.

**Fix:** JSON output now surfaces `fallback_used`, `actual_source`, and warnings when SEC fallback occurs.

### 7. requests Dependency Was Undeclared
The SEC client imports `requests` but it was not in pyproject.toml dependencies.

**Fix:** Added `"requests>=2.28.0"` to dependencies.

## What Is Now Truly Strict vs Still Heuristic

**Strict (proven by tests):**
- Period matching rejects cross-period facts
- `reconciled_pass` requires `exact_period` mode
- Wrong-year facts are excluded
- Scale inference auto-resolves "in millions"
- period_match_mode is always surfaced

**Still heuristic:**
- 10-Q quarter derivation when company facts don't have a filed-date match
- Non-calendar fiscal year detection without company facts
- Filing-month → fiscal-year mapping
