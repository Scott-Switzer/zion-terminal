# Hardening Pass Notes — v0.4.1

## Bugs Found and Fixed

### B1: Dead code and routing regressions
- **Dead `import re` in sec_edgar.py**: Removed. The `re` module was only used by the deleted `_html_to_markdown()` function.
- **Auto-routing didn't route `financials` to SEC**: `_find_adapter_for_task()` in `agent.py` only routed `filings`, `company_facts`, `filing_markdown` to SEC. Added `financials` to the SEC-first list with Yahoo fallback.
- **Convenience method `fetch_financials()` hardcoded Yahoo**: Updated to prefer SEC when available.

### B2: Strict mode printed invalid data
- **Bug**: `_run_and_output()` always printed formatted data, THEN exited nonzero. Users could pipe invalid data to downstream tools.
- **Fix**: When `resp.success` is False, errors are printed to stderr and data output is suppressed. Exit code is nonzero.

### B3: Verification status taxonomy
- Verification statuses are now clearly defined: `passed`, `warning`, `failed`, `unavailable`, `skipped`, `not_run`.
- Each status maps to specific conditions in `FilingVerifier`.

### B4: Benchmarks didn't test live path
- Added `TestLivePipelineBenchmarks` class that benchmarks `FilingPipeline.process()` — the actual live path, not just the converter helper.

### B5: Docs drift
- sec_edgar.py docstring still referenced "Phase 1.5 experimental" and implied a private converter. Updated.
- Test count in docs updated from 277 to 290+.

### B6: Company facts output was raw JSON
- Added `_format_company_facts_md()` to the formatter — produces structured markdown with header, metadata, and a sample facts table.

### B7: Test blind spots
- Added `test_hardening.py` with 11 tests covering: dead code checks, auto-routing verification, strict mode CLI behavior, live pipeline on all fixtures, wrapper filing handling, company facts formatting, NL query routing.

## Regressions Found
1. `_find_adapter_for_task` was a silent regression from the architecture pass — the SEC-first intent was documented but not implemented in the auto-router.
2. Strict mode behavior was never explicitly defined or tested for CLI output suppression.

## What Remains Uncertain
- NL query path still uses parser-assigned source names, which may default to Yahoo for financials. The auto-router catches this for task-level routing, but the explicit `source` field in parser tasks takes precedence. This needs a future pass to make the parser SEC-aware.
- Cross-source reconciliation logic is still hooks-only.
