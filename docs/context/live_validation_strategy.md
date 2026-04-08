# Live Validation Strategy

## Validation Layers

### Layer 1: Retrieval Validation (existing)
- Non-empty data items
- Source attribution present
- Price/volume bounds for market data
- Chronological ordering for time series

### Layer 2: Filing Pipeline Verification (v0.4.0)
- Structural checks (content exists, sections found)
- Expected section presence for form type
- Converter engine tracking (dom vs regex)
- Pipeline metadata completeness

### Layer 3: XBRL Verification (optional)
- Schema conformance via Arelle
- Fact extraction and count
- Period and unit consistency

### Layer 4: Cross-Source Reconciliation (future)
- Compare SEC-derived financials against Yahoo Finance
- Flag significant discrepancies
- All comparisons labeled "heuristic"

## Verification Levels

| Level | Description | Status |
|---|---|---|
| L0 | Data exists and has structure | Implemented |
| L1 | Source attribution and bounds | Implemented |
| L2 | Filing structural completeness | Implemented (v0.4.0) |
| L3 | XBRL schema validation | Implemented (optional dep) |
| L4 | Cross-source reconciliation | Hook exists, logic pending |
| L5 | Historical comparison | Not implemented |

## Honesty Policy
- If a check is heuristic, it is labeled heuristic
- If verification is unavailable, status says "unavailable", not "passed"
- Missing Arelle = "skipped", not "validated"
