# ADR-0020: Honest Verification Status Semantics

**Date:** 2026-04-08  
**Status:** Accepted

## Context

The filing pipeline verification was reporting `"passed"` when only structural checks (markdown exists, sections found) had run. No XBRL validation and no cross-source comparison had been performed. This made `"passed"` misleading — it implied full verification when only structural completeness was checked.

## Decision

Changed verification status semantics:

| Status | Meaning |
|--------|---------|
| `structural_only` | Only structural checks ran. No XBRL, no cross-source. |
| `passed` | Structural passed AND at least one deeper check (XBRL or cross-source) also passed |
| `partial` | Some checks passed, others failed or unavailable |
| `failed` | Structural checks failed |
| `not_run` | Verification did not execute |

Added `verification_depth` field: `"none"`, `"structural_only"`, `"xbrl"`, `"cross_source"`, `"full"`.

## Consequences

- `"passed"` now means something meaningful — not just "markdown exists"
- Consumers can distinguish structural-only from full verification
- No false sense of data quality from structural-only runs
- Existing tests updated to expect `"structural_only"` instead of `"passed"`
