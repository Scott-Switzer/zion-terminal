# ADR-0017: XBRL Ground Truth Reconciliation

**Status:** Accepted (partially implemented)  
**Date:** 2026-04-08  
**Author:** Zion Terminal development team

---

## Context

The filing pipeline converts SEC EDGAR HTML filings to markdown. The conversion process can introduce errors:
- Numbers scaled incorrectly (e.g. millions vs. actual dollars).
- Sign inversions (losses reported as positive).
- Numbers dropped entirely (e.g. tables not converted).
- Labels detached from values.

We need an automated mechanism to detect these errors. SEC EDGAR provides structured XBRL data for most filings, which gives us a ground truth for numeric values. The question is: how to use it?

---

## Decision

Use Arelle to extract XBRL facts from SEC filings. Map those facts to a canonical schema (`CanonicalFact`). Compare them against numeric values extracted from the markdown output. Report match status per fact.

### Match Types

| Status | Meaning |
|---|---|
| `matched` | XBRL value and markdown value agree within 0.1% relative tolerance. |
| `scale_mismatch` | Values differ by exactly 1000× or 1,000,000× — a units/scaling error. |
| `sign_mismatch` | Values are equal in magnitude, opposite in sign — a negation error. |
| `label_mismatch` | The concept label appears in the markdown, but the numeric value does not match and does not fit a scale or sign pattern. |
| `missing` | The XBRL concept was not found in the markdown at all. |

### Arelle is Optional

Arelle is declared as an optional dependency. When Arelle is not installed:
- XBRL reconciliation is skipped.
- The relevant `ValidationCheck` entries have `status="unavailable"`.
- The composite benchmark score is computed from the remaining metrics only.
- No error is raised; a warning is logged.

This is consistent with ADR-0008 (Arelle validation boundaries) and ADR-0013 (Arelle in live validation).

### Implementation Modules

- `src/zion_terminal/verification/fact_mapping.py` — Arelle → `CanonicalFact`.
- `src/zion_terminal/verification/markdown_extractor.py` — markdown → `ExtractedValue` list.
- `src/zion_terminal/verification/reconciler.py` — comparison → `ReconciliationResult`.

---

## Alternatives Considered

### Use the SEC Company Facts JSON API instead of Arelle
The SEC provides a JSON endpoint (`data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`) that does not require Arelle. This is used for `company_facts` queries. However, it does not provide inline XBRL fact-to-filing mapping — it aggregates facts across all filings. For reconciliation against a specific filing's markdown, Arelle's per-filing parsing is more precise. Rejected for the reconciliation use case, though the JSON API remains the source for `company_facts` responses.

### Manual regex parsing of XBRL XML
Would work without Arelle but requires maintaining a custom XBRL parser. Arelle is the authoritative, maintained implementation. Rejected.

### Skip XBRL reconciliation entirely
Viable in the short term — conversion errors are detected by structural checks (section count, content presence). But structural checks cannot detect numeric errors. Rejected as a permanent policy.

### LLM-based numeric verification
Ask an LLM to compare the markdown values against expected ranges. Non-deterministic, expensive, and introduces a dependency on LLM availability. Rejected for automated reconciliation. Could be used for sampling / spot-checking.

---

## Tradeoffs

**Chosen approach (Arelle + canonical fact schema):**
- Pro: Arelle is the industry-standard XBRL processor. Its output is reliable.
- Pro: Optional dependency means no hard requirement on Arelle in CI.
- Pro: Per-fact match status is actionable — `scale_mismatch` points directly to a unit conversion bug.
- Con: Arelle is a large dependency (~50 MB). Not all users will install it.
- Con: XBRL data is not available for all filings (some older or non-US filings lack XBRL).
- Con: Label matching between XBRL concepts and markdown row labels is fuzzy and can produce false matches.
- Con: End-to-end integration is not yet complete (see Implementation Status below).

---

## Implementation Status

| Component | Status |
|---|---|
| `fact_mapping.py` | Implemented |
| `markdown_extractor.py` | Implemented |
| `reconciler.py` | Implemented |
| Integration into `FilingPipeline.process()` | Pending |
| Integration into `ValidationAgent` | Pending |
| Benchmark runner integration | Pending |
| Arelle in CI | Pending |

---

## Consequences

1. `ValidationAgent` will eventually expose XBRL reconciliation results as `ValidationCheck` entries with `check_name="xbrl_reconciliation"`.
2. When Arelle is absent, all XBRL checks report `status="unavailable"`.
3. The `match_rate` from `ReconciliationResult` will feed into the composite benchmark score as `xbrl_match_rate` (weight: 45%, as per ADR-0016).
4. `FilingPipelineResult.verification["xbrl"]` will include the `ReconciliationResult` when available.

---

## Follow-up

- [ ] Wire `reconciler.py` into `FilingPipeline.process()`.
- [ ] Wire `reconciler.py` into `ValidationAgent.validate_retrieval()`.
- [ ] Add Arelle to optional CI dependencies.
- [ ] Add `xbrl_match_rate` to `docs/benchmarks/latest.json`.
- [ ] Improve label matching to reduce false positives.

---

## Reference

- Implementation: `src/zion_terminal/verification/`
- Context: `docs/context/xbrl_reconciliation_design.md`
- Context: `docs/context/markdown_extraction_strategy.md`
- Related ADRs: ADR-0008, ADR-0013, ADR-0016
