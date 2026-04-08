# SEC → Markdown → Verification Pipeline

## Overview

A filing moves through these stages:

1. **Retrieval** — SEC EDGAR API fetches the raw filing document (HTML/iXBRL)
2. **Normalization** — Raw content is cleaned (scripts/styles removed)
3. **Markdown Conversion** — DOM-based conversion preserving structure (tables, headings, lists)
4. **Segmentation** — Split into standard SEC sections (Item 1, Item 1A, Item 7, etc.)
5. **Verification** — Three tiers:
   - Structural: content exists, sections found, expected sections present
   - XBRL/Arelle: fact extraction and schema validation (optional dependency)
   - Cross-source: compare against Yahoo Finance data (when available)
6. **Validation Output** — Machine-readable verification result attached to response
7. **Final Response** — `OrchestratorResponse` with data, validation, and pipeline metadata

## Verification Tiers

### Tier 1: Structural (always runs)
- Markdown content exists and has reasonable length
- Sections were identified
- Expected sections for the form type are present (e.g., Items 1, 7, 8 for 10-K)

### Tier 2: XBRL/Arelle (when installed)
- Validates XBRL instance document against schema
- Extracts structured facts
- Reports fact count and any validation errors
- Degrades gracefully: if Arelle is not installed, status = "unavailable"

### Tier 3: Cross-source (when data available)
- Compares filing-derived values against Yahoo Finance
- Currently a hook — automated reconciliation is future work
- All cross-source checks are labeled "heuristic"

## What This Is NOT

- Not a guarantee of financial accuracy
- Not a replacement for Bloomberg validation
- Cross-source checks are informational, not authoritative
- XBRL validation is schema validation, not semantic validation
