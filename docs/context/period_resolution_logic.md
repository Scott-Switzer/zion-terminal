# Period / Quarter / Fiscal Year Resolution Logic

## The Problem

SEC filings must be reconciled against XBRL facts from the **same fiscal period**.
A 2022 10-K should only compare against FY2022 XBRL data, not FY2023.

## Resolution Strategy

The function `_derive_fiscal_period()` in `pipeline/verification.py` determines
the target fiscal year and period using multiple evidence sources:

### For 10-K Filings

1. **Derive candidate FY** from filing date: if filed after June, FY = filing year;
   if filed before June, FY = filing year - 1 (most 10-Ks are filed in Q4/Q1).
2. **Validate against company facts**: search for any XBRL entry with
   `fy == candidate_fy` and `fp == "FY"`. If found → `exact_period`.
3. **Fallback**: if no XBRL validation possible → `heuristic`.

### For 10-Q Filings

1. **Derive candidate FY** from filing date (same logic as 10-K).
2. **Strategy 1 — exact filed-date match**: search company facts for an entry
   with `fy == candidate_fy`, `fp` starting with "Q", and `filed == filing_date`.
   If found → `exact_period`.
3. **Strategy 2 — quarter frequency analysis**: count how many XBRL entries
   exist for each quarter in the candidate FY. Use filing month to select the
   most likely quarter. → `heuristic`.
4. **Fallback**: if no quarter can be determined → `year_only`.

## Known Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| Non-calendar fiscal years | Companies ending FY in June/March get wrong candidate_fy | Validated against company facts when available |
| No filed-date match in XBRL | Exact quarter cannot be determined | Falls back to heuristic with explicit mode tracking |
| Filing month heuristic | ~40-day filing delay makes month→quarter mapping approximate | Uses XBRL entry counts as additional signal |

## Period Match Mode Semantics

- `exact_period`: Both FY and FP confirmed from XBRL metadata. **Only mode that allows `reconciled_pass`.**
- `heuristic`: FY/FP derived from filing date. Caps status at `reconciled_partial`.
- `year_only`: FY determined but quarter unknown. Caps status at `reconciled_partial`.
- `no_filing_date`: No filing date available. No reconciliation runs.
