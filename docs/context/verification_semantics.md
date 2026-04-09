# Verification Semantics — What Each Status Means

## Verification Status Values

| Status | Meaning | Requires Arelle? |
|--------|---------|-----------------|
| `reconciled_pass` | XBRL facts matched markdown facts, ≥80% match rate, **exact period** | No |
| `reconciled_partial` | Reconciliation ran but match rate 50-80% OR period is heuristic | No |
| `reconciled_fail` | Reconciliation ran, match rate <50% | No |
| `structural_only` | Only structural checks ran (markdown exists, sections found) | No |
| `failed` | Structural checks failed (empty/short markdown) | No |
| `passed` | Structural + Arelle/cross-source passed | Yes (for Arelle) |

## Period Match Modes

| Mode | Meaning | Allows `reconciled_pass`? |
|------|---------|--------------------------|
| `exact_period` | FY and FP confirmed from XBRL metadata | Yes |
| `heuristic` | FY/FP derived from filing date heuristics | No — capped at `reconciled_partial` |
| `year_only` | FY determined but quarter unknown | No — capped at `reconciled_partial` |
| `no_filing_date` | No filing date available | No reconciliation runs |

## Company-Facts vs Arelle Verification

| Feature | Company-Facts Reconciliation | Arelle XBRL Validation |
|---------|------------------------------|----------------------|
| Dependency | None (SEC JSON API) | `arelle-release` package |
| What it proves | Markdown preserves same financial facts as XBRL | Instance document is schema-conformant |
| Coverage | Core US-GAAP concepts (Revenue, Net Income, Assets, etc.) | All XBRL facts including custom extensions |
| Period matching | Strict fiscal year/period filtering | Full context-aware period handling |
| Scale detection | Auto-detects "in millions" etc. | Not applicable |

## What "Reconciled Pass" Actually Proves

When verification reports `reconciled_pass` with `exact_period`:
1. Company facts were fetched from SEC's XBRL API
2. Facts were filtered to the **exact same fiscal period** as the filing
3. Markdown tables were parsed for numeric values
4. Scale context ("in millions") was detected and applied
5. XBRL facts were matched to markdown labels via alias mapping
6. ≥80% of compared facts matched within 2% tolerance
7. No hidden period fallback occurred

## What It Does NOT Prove

- That every single number in the filing was checked (only core US-GAAP concepts)
- That non-financial text is accurate
- That the XBRL instance document is schema-conformant (requires Arelle)
- That custom extension taxonomy facts are correct
