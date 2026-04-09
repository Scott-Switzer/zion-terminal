# ADR-0022: Convergence + Correctness Hardening (v0.7.0)

**Date:** 2026-04-08  
**Status:** Accepted

## Context

The repo needed to (1) fix critical correctness bugs in reconciliation, (2) move toward team-compatible interfaces, and (3) preserve the stronger SEC/XBRL pipeline.

## Changes

### Correctness Fixes

1. **Strict Period Matching** — `_company_facts_to_canonical()` no longer takes the "most recent" XBRL entry. It now accepts `target_fy` and `target_fp` parameters and only returns facts from the matching fiscal period. If no match, it returns empty (no silent fallback).

2. **Scale/Header Inference** — `detect_scale_context()` scans markdown for phrases like "in millions", "in thousands", "in billions" and applies the multiplier before reconciliation. This prevents false scale mismatches when filings report in millions but XBRL is in raw dollars.

3. **Period Tracking** — VerificationResult now includes `period_matched` (e.g. "FY2023") so users know which fiscal period was reconciled.

### Team-Compatible Architecture

4. **CleanedDocument** (`models/documents.py`) — A stable document model that teammates can use without knowing about the filing pipeline internals.

5. **DocumentStore** (`cache/doc_store.py`) — SQLite-backed persistent storage for processed documents, complementing the existing diskcache TTL layer.

6. **Schema Contracts** (`models/contracts.py`) — `RetrievalRequest` and `CleanedResult` define stable interfaces at agent boundaries.

7. **CLI Reconciliation Summary** — Filing-markdown output now shows verification status, match rate, period, and mismatch counts.

### Arelle Testing

8. **Arelle test profile** — `test_arelle_xbrl.py` uses `pytest.importorskip` to skip (not fail) when Arelle is not installed. When available, it verifies correct XBRL loading and fact extraction.

## Architecture Decisions

### What was adopted from team repo
- `CleanedDocument` abstraction
- SQLite processed-document persistence pattern
- Simpler schema contracts at agent boundaries

### What was preserved from private repo
- Direct SEC client + edgartools hybrid
- XBRL↔markdown reconciliation pipeline
- Rich verification status semantics
- Filing pipeline (converter → segmenter → verifier)
- Historical filing retrieval

## Consequences

- Period matching is strict — cross-period reconciliation is now impossible
- Scale inference auto-resolves "in millions" — fewer false mismatches
- Teammates can use CleanedDocument and DocumentStore without pipeline knowledge
- Arelle tests skip cleanly when not installed
