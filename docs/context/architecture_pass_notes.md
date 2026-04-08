# Architecture Pass Notes — v0.4.0

## What Changed

### SEC becomes primary source
- `get_financials()` now defaults to SEC EDGAR, not Yahoo Finance
- Yahoo Finance is available via `--source yahoo` flag
- Source role model (`models/source_roles.py`) codifies the hierarchy

### Unified filing pipeline
- Single `FilingPipeline` class orchestrates: conversion → segmentation → verification
- SEC adapter's private `_html_to_markdown()` deleted
- All filing conversion routes through `pipeline/filing_pipeline.py`
- Regression test prevents reintroduction of private converter

### Verification in live path
- `FilingVerifier` runs structural, XBRL, and cross-source checks
- Arelle integration via `XBRLVerifier` (optional dependency)
- Verification metadata attached to pipeline results
- Graceful degradation when Arelle not installed

### Deterministic synthesis
- Instance-level seeded RNG replaces module-level `random`
- Same seed → identical output (no-LLM mode)
- Query string hashed for default seed when no explicit seed given

### Yahoo repositioned
- Market data (quotes, history) remains Yahoo-primary
- Financial statements default to SEC
- Yahoo available as explicit fallback via CLI flag
- Source roles documented in `source_roles.py` and docs

## What Remains Uncertain
- Wrapper filing detection and resolution
- Automated cross-source reconciliation logic
- iXBRL inline parsing (Arelle supports it, not yet wired)
- Historical comparison across multiple filings
- TOC vs. real section discrimination in edge cases

## What Was NOT Changed
- Quote/history paths still use Yahoo Finance (correct per source hierarchy)
- FRED macro data path unchanged
- CLI command names unchanged
- Cache layer unchanged
- Output formatters unchanged (except filing metadata handling)
