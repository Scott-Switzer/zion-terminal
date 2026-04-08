# Changelog

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
