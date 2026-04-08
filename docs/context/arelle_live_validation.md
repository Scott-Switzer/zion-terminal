# Arelle in Live Validation

## Status
Arelle is integrated into the live filing verification pipeline as an optional dependency.

## How It Works

When a filing is processed through the unified pipeline:

1. If a XBRL URL is available for the filing, the `FilingVerifier` calls `XBRLVerifier.validate_url()`
2. The `XBRLVerifier` uses Arelle's `Cntlr` to load and validate the XBRL instance
3. Facts are extracted using `fact.concept.qname.localName` (API-aligned as of v0.3.1)
4. Period info is extracted from `context.isStartEndPeriod` / `context.isInstantPeriod`

## Graceful Degradation

If Arelle is not installed (`pip install zion-terminal[xbrl]`):
- `xbrl_status` = "unavailable"
- A warning is recorded: "Arelle not installed — XBRL verification skipped"
- The pipeline continues normally without XBRL validation
- No errors are raised

## What Arelle Validates
- Schema conformance of the XBRL instance document
- Fact extraction (concept name, value, context, unit, period)
- Loading errors from the XBRL processor

## What Arelle Does NOT Validate
- Semantic correctness of financial values
- Consistency between XBRL facts and narrative text
- Completeness of disclosure

## Installation
```bash
pip install zion-terminal[xbrl]
```

## Testing
Positive-path tests use mocks that simulate Arelle's `ModelFact` and `ModelContext` objects. When Arelle is not installed, tests verify graceful degradation.
