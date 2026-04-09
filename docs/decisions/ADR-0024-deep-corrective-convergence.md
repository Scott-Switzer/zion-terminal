# ADR-0024: Deep Corrective + Convergence Pass (v0.8.0)

**Date:** 2026-04-08  
**Status:** Accepted

## Context

The private repo had reached v0.7.1 with strong SEC/XBRL capabilities but
several truthfulness and integration gaps remained:
1. Benchmark notes said "XBRL reconciliation unavailable" when Arelle absent,
   even though company-facts reconciliation still ran successfully.
2. CleanedDocument and DocumentStore existed but were not wired into any
   live orchestrator path — pure scaffolding.
3. Quarter derivation for 10-Q used only filing-month heuristics.
4. Documentation about verification semantics, period resolution, and
   team convergence was missing.

## Changes

### Benchmark Truthfulness
- Notes now correctly distinguish "Arelle XBRL instance validation" from
  "company-facts reconciliation" — the latter works without Arelle.
- Summary includes `reconciliation_tested` and `reconciliation_passed` counts.

### Live Compatibility Integration
- Orchestrator now creates and exposes `DocumentStore` via `doc_store` property.
- Orchestrator closes DocumentStore on `close()`.
- `to_cleaned_document()` remains the bridge from pipeline internals to
  team-compatible `CleanedDocument` objects.

### Quarter Derivation Improvement
- Added Strategy 2 for 10-Q: counts available XBRL entries per quarter in
  the candidate FY and uses filing-month correlation to select the most likely
  quarter. Still heuristic but uses more evidence than filing month alone.

### Comprehensive Documentation
- `docs/convergence/private_vs_team_continuity.md` — full comparison
- `docs/context/verification_semantics.md` — what each status means
- `docs/context/period_resolution_logic.md` — FY/FP derivation details
- `docs/context/benchmark_interpretation.md` — how to read benchmarks
- `docs/runbooks/arelle_verification_profile.md` — how to run strict XBRL tests

## Why These Changes Fit This Repo

The private repo's technical advantage is its richer verification pipeline.
Making that pipeline's output truthful and well-documented is more valuable
than simplifying it to match the team repo's simpler validation model.

## Continuity With Shared Team Repo

| Team Repo Concept | Private Repo Equivalent |
|-------------------|------------------------|
| `CleanedDocument` | `models/documents.py` (compatible) |
| `Cache.processed_documents` table | `DocumentStore` (compatible pattern) |
| Cleaning agent | Filing pipeline with `to_cleaned_document()` |
| `ValidationResult` | `VerificationResult` (richer) |
