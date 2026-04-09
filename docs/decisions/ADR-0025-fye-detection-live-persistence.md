# ADR-0025: FYE Detection + Live Document Persistence (v0.8.1)

**Date:** 2026-04-08  
**Status:** Accepted

## Context

Two problems remained from v0.8.0:
1. Fiscal year derivation used a fixed `month > 6` heuristic that fails for
   non-calendar fiscal year companies (e.g. Microsoft ends FY in June).
2. DocumentStore was initialized in the orchestrator but never actually used
   to store processed documents — the live path bypassed it entirely.

## Changes

### FYE Month Detection
Added `_detect_fiscal_year_end_month()` which examines the `"end"` field of
annual (FP="FY") entries in company facts to determine the company's fiscal
year end month. For Apple, this detects month 9 (September). For Microsoft,
month 6 (June). This replaces the hardcoded `month > 6` assumption.

**Evidence used:** The SEC companyfacts JSON includes `"end": "2023-09-30"`
for every FY entry, directly encoding when the fiscal year ended.

### Live Document Persistence
`get_filing_markdown()` now calls `_persist_filing_document()` after
successful processing. This creates a `CleanedDocument` from the response
data and stores it in `DocumentStore`. The `persist` parameter (default True)
controls this behavior.

**Why this matters for team continuity:** The shared team repo's `Cache`
class has a `processed_documents` table. The private repo's `DocumentStore`
fills the same role. Documents stored here can be retrieved by doc_id without
re-processing the filing.

## Why These Changes Fit This Repo

The private repo's FYE detection uses the same company facts data it already
fetches for reconciliation — no new API calls needed. The persistence
integration makes the compatibility layer live instead of scaffolding.

## What Remains Heuristic

| Aspect | Status |
|--------|--------|
| FY derivation with company facts | Exact (uses XBRL period-end dates) |
| FY derivation without company facts | Heuristic (month > 6 rule) |
| 10-Q quarter with exact filed-date match | Exact |
| 10-Q quarter without filed-date match | Heuristic (month-based) |
| Non-calendar FY detection with company facts | Works (detects FYE month) |
| Non-calendar FY detection without company facts | Heuristic (assumes Dec FY) |
