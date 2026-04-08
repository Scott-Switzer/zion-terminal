# Changelog

## v0.4.2 — Prompt A Completion Pass (2026-04-08)

### Bug Fixes
- Fixed SEC financials schema mismatch: adapter now outputs `line_items` + `statement_type` (formatter-compatible)
- Fixed Arelle wrapper to use correct API: `ModelManager.initialize()`, `ValidateXbrl`, `fact.xValue`, `unit.measures`

### New Features
- TOC discrimination in segmenter: filters dense Item clusters in first 20% as TOC entries
- Synthesis regeneration scaffold: `generate_with_retry()` retries with perturbed seeds on validation failure
- Extended validation schema: `checks_warned`, `checks_skipped`, `checks_unavailable` fields

### Documentation
- 3 new context docs: what_not_to_break, project_intent, design_principles
- 3 new agent handoff docs: next_steps, current_risks, benchmark_readme
- 2 new/updated ADRs: ADR-0007 (TOC heuristics), ADR-0008 (Arelle boundaries)
- TOC-heavy HTML fixture

### Tests
- 302 tests (up from 291): 11 new tests for schema alignment, TOC filtering, regeneration, validation fields

## v0.4.1 — Hardening Pass (2026-04-08)

### Bugs Fixed
- Removed dead `import re` from sec_edgar.py (leftover from deleted converter)
- Fixed auto-routing: `financials` action now routes to SEC EDGAR first, with Yahoo fallback
- Fixed convenience method `fetch_financials()` to prefer SEC when available
- Fixed strict mode: invalid data is no longer printed to stdout; errors go to stderr
- Updated sec_edgar.py docstring to reflect unified pipeline architecture

### Improvements
- Added `_format_company_facts_md()` formatter for structured XBRL facts output
- Added 11 hardening tests covering dead code, routing, strict mode, live pipeline, formatting
- Added 2 live pipeline benchmarks (small fixture, div-header fixture)

### Documentation
- 6 new context docs (hardening notes, known limitations, testing notes, strict mode, validation schema, company facts)
- 3 new benchmark docs (README, methodology, known gaps)
- Updated implementation status and open questions

### Tests
- 290+ tests (up from 277)

## v0.4.0 — Architecture Refactor (2026-04-08)

### Architecture Changes
- SEC EDGAR is now the primary source for financial statements (ADR-0010)
- Single unified filing pipeline replaces duplicate conversion paths (ADR-0011)
- SEC → Markdown → Verification is the live pipeline (ADR-0012)
- Arelle integrated into live verification (ADR-0013)
- Yahoo Finance repositioned as verification/cross-check layer (ADR-0014)
- Synthesis agent made deterministic with seeded RNG (ADR-0015)

### New Modules
- `models/source_roles.py` — source role definitions and hierarchy
- `pipeline/filing_pipeline.py` — unified filing pipeline
- `pipeline/verification.py` — structural + XBRL + cross-source verification

### Breaking Changes
- `get_financials()` now defaults to SEC EDGAR (use `--source yahoo` for Yahoo)
- SEC adapter's private `_html_to_markdown()` removed

### Tests
- 270+ tests (up from 249)
- New test files: test_source_roles, test_filing_pipeline, test_verification, test_synthesis_determinism

## v0.3.1 — Follow-up Patch Set (2026-04-08)
- 8 bug fixes, expanded benchmarks, 249 tests

## v0.3.0 — Deep Refactor (2026-04-08)  
- Pipeline, validation, strict mode, 218 tests

## v0.2.0 — Surgical Refactor
- Honest CLI, modular agents, 155 tests

## v0.1.0 — Initial Implementation
- 74 tests
