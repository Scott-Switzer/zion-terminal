# ADR-0021: Live XBRL↔Markdown Fact Reconciliation

**Date:** 2026-04-08  
**Status:** Accepted

## Context

The repo had three verification modules (`fact_mapping.py`, `markdown_extractor.py`, `reconciler.py`) that were unit-tested but never called from the live filing pipeline. The SEC adapter passed `metadata={"ticker", "form", "source"}` to the pipeline — never `xbrl_url` or `company_facts`. This meant verification was always `structural_only`, which only checked that markdown existed and had sections — it never verified that financial facts matched the source.

## Decision

Wire the reconciler into the live filing-markdown path using SEC company facts JSON (no Arelle dependency required):

1. **SEC adapter** now fetches company facts from `data.sec.gov/api/xbrl/companyfacts/` and passes them as `metadata["company_facts"]`
2. **Filing pipeline** passes company facts to the verifier
3. **FilingVerifier** converts company facts to `CanonicalFact` objects, extracts markdown values, and runs the reconciler
4. **Reconciliation results** are included in verification output with match counts, mismatch details, and scale/sign detection

## Architecture

```
SEC Adapter
  ├── Fetch filing HTML → pipeline (conversion + segmentation)
  ├── Fetch company facts → pipeline metadata
  └── Discover XBRL URL → pipeline metadata

Filing Pipeline
  ├── Stage 1: HTML → Markdown (converter)
  ├── Stage 2: Markdown → Sections (segmenter)
  └── Stage 3: Verification
        ├── Structural checks
        ├── XBRL/Arelle validation (optional)
        ├── XBRL↔Markdown reconciliation (NEW — uses company facts)
        └── Cross-source checks (optional)
```

## Reconciliation Design

- Extracts core US-GAAP concepts from company facts (Revenue, Net Income, Assets, etc.)
- Extracts numeric values from markdown tables
- Matches XBRL concepts to markdown labels using alias mappings
- Compares values with 2% tolerance
- Detects: exact matches, scale mismatches (1000x, 1Mx), sign mismatches, missing facts
- Status: `reconciled_pass` (≥80% match), `reconciled_partial` (50-80%), `reconciled_fail` (<50%)

## Why Company Facts Instead of Arelle

- Company facts JSON requires no additional dependencies
- Available for all SEC filers (10K, 10Q, 8K)
- Contains all XBRL-tagged facts across all filings
- Arelle is still supported for deeper validation but is not required

## Consequences

- Verification is now meaningful, not just structural
- The live path proves markdown preserves source financial facts
- Scale/sign mismatches are automatically detected
- No new dependencies required
- Test corpus includes exact-match and scale-mismatch fixtures
