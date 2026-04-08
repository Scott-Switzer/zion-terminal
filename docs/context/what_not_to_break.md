# What Not To Break

This file documents invariants and behaviors that future changes must preserve.

## Architectural Invariants

1. **Single filing pipeline**: All SEC filing conversion MUST flow through `pipeline/filing_pipeline.py → converter.py → segmenter.py`. The SEC adapter must NOT have its own converter. Regression test: `test_pipeline_is_the_only_converter_path`.

2. **SEC-first financials**: `get_financials()` defaults to SEC EDGAR. The auto-router in `_find_adapter_for_task()` routes `action="financials"` to SEC first with Yahoo fallback. Regression test: `test_financials_auto_routes_to_sec`.

3. **Strict mode safety**: When `resp.success=False`, the CLI suppresses data output and sends errors to stderr. Regression test: `test_strict_failure_does_not_print_data`.

4. **Deterministic synthesis**: `SynthesisAgent` uses `self._rng` (instance-level `random.Random`), not module-level `random`. Same seed → identical output. Regression test: `test_same_seed_same_output`.

5. **No LLM required**: The system works fully with `LLM_PROVIDER=none`. LLM is optional for ambiguous query parsing and press release generation only.

6. **Arelle optional**: XBRL validation degrades gracefully when Arelle is not installed. Never crash, never silently skip — mark status as "unavailable".

## Schema Invariants

7. **SEC financials schema**: Must include `line_items` dict and `statement_type` string for formatter compatibility. Not `data` dict and `statement` string.

8. **Response metadata**: `OrchestratorResponse.metadata` must include `source_preference` and `source_role` for financials queries.

9. **Validation result schema**: Must include `checks_run`, `checks_passed`, `checks_warned`, `checks_failed`, `checks_skipped`, `checks_unavailable`.

## CLI Invariants

10. **All commands work**: `query`, `quote`, `history`, `financials`, `filings`, `macro`, `info`, `filing-markdown`, `company-facts`, `synthesis`. Regression test: `test_all_subcommands_exist`.

11. **Version consistency**: `pyproject.toml`, `__init__.py`, and `test_packaging.py` must agree on version string.
