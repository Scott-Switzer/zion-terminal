# Private Repo ↔ Shared Team Repo Continuity Plan

## Overview

The private repo (`Scott-Switzer/zion-terminal`) is the stronger technical
base. The shared team repo (`zion-terminal/zion-terminal`) has cleaner
architecture boundaries. This document explains how they converge.

## Architecture Comparison

### Shared Team Repo
```
retrieval → cleaning → validation → cache → schemas
```
- `DataResponse` — raw retrieval output
- `CleanedDocument` — processed document with sections + tables
- `ValidationResult` — is_valid + errors + warnings
- `Cache` — SQLite-backed, stores raw responses + processed docs

### Private Repo
```
orchestrator → retrieval → pipeline → verification → cache
                              ↓
                    converter → segmenter → verifier → reconciler
```
- Richer models: SECFiling, CanonicalFact, ExtractedValue, ReconciliationReport
- Two cache layers: diskcache (TTL) + DocumentStore (SQLite persistent)
- Direct SEC client for ticker→CIK, filing discovery, company facts
- XBRL↔markdown reconciliation pipeline

## What Was Adopted From Team Repo

| Feature | Team Repo Source | Private Repo Integration |
|---------|-----------------|------------------------|
| `CleanedDocument` model | `src/models/schemas.py` | `models/documents.py` — compatible fields |
| SQLite document store | `src/cache/cache.py` | `cache/doc_store.py` — same pattern |
| Schema contracts | `QueryIntent`, `DataResponse` | `contracts.py` — `RetrievalRequest`, `CleanedResult` |
| Cleaning boundary | `agents/cleaning/` | `to_cleaned_document()` on pipeline result |

## What Was Preserved From Private Repo

| Feature | Reason |
|---------|--------|
| Direct SEC client | More reliable CIK resolution, historical filtering |
| Filing pipeline | Stronger HTML→markdown→segments→verification |
| XBRL reconciliation | Company-facts-based fact comparison (no Arelle needed) |
| Rich verification status | `reconciled_pass` / `structural_only` / etc. |
| Historical retrieval | Year/quarter filtering via SEC submissions API |

## How a Teammate Should Use This Repo

1. **Retrieve a filing:** `orchestrator.get_filing_markdown("AAPL", form="10-K")`
2. **Get a CleanedDocument:** Access `pipeline_metadata` from the response, or use `FilingPipelineResult.to_cleaned_document()`
3. **Persist a document:** `orchestrator.doc_store.store(cleaned_doc)`
4. **Check verification:** Read `pipeline_metadata.verification` for reconciliation status
5. **Build a new retrieval handler:** Implement `BaseAdapter.fetch()` pattern

## Remaining Divergence

| Area | Status |
|------|--------|
| Cleaning agent as a separate class | Private repo uses pipeline internally, exposes `to_cleaned_document()` |
| Single unified Cache class | Private repo has diskcache + DocumentStore side-by-side |
| QueryIntent model | Private repo uses `ParsedIntent` with richer fields |
