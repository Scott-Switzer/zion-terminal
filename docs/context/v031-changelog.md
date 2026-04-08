# v0.3.1 Changelog — Follow-up Patch Set

**Date:** 2026-04-08
**Commit base:** `b6a140a` (v0.3.0)

## Bug Fixes

### 1. Synthesis command strict mode (cli.py)
- **Bug:** `synthesis` command lacked `@click.pass_context` and never passed `strict` to `_build_orchestrator()`.
- **Fix:** Added `@click.pass_context` decorator and `ctx.obj.get("strict", False)` argument.
- **Test:** `test_synthesis_passes_strict` verifies `--strict` flows through.

### 2. Converter metadata always reported "dom" (pipeline/converter.py)
- **Bug:** `"converter": "dom" if "bs4" in str(type(self)) or True else "regex"` — the `or True` made this always evaluate to `"dom"`.
- **Fix:** Track `used_dom` boolean based on whether the bs4 import succeeded.
- **Tests:** `test_converter_metadata_reports_dom`, `test_converter_metadata_reports_regex_without_bs4`.

### 3. README/config mismatches (README.md)
- **Bug:** README showed `OLLAMA_BASE_URL=http://localhost:11434` (missing `/v1`), `OLLAMA_MODEL=llama3` (should be `llama3.1`), `CACHE_TTL=3600` (should be `CACHE_TTL_SECONDS`).
- **Fix:** Aligned README with `.env.example` and `settings.py`.

### 4. _llm_assisted not surfaced (orchestrator.py)
- **Bug:** `parsed.params["_llm_assisted"]` was set by the parser but never propagated to `OrchestratorResponse.metadata`.
- **Fix:** Orchestrator checks for the flag and sets `metadata["llm_assisted"] = True` when present.
- **Tests:** `test_llm_assisted_surfaced_when_set`, `test_llm_assisted_absent_when_not_set`.

### 5. Arelle wrapper API mismatch (pipeline/xbrl.py)
- **Bug:** `fact.concept` treated as dict (`.get("name", "")`), but Arelle uses `ModelConcept` objects with `.qname.localName`. Period info accessed via non-existent `context.period` sub-object.
- **Fix:** `_extract_concept_name()` uses `fact.concept.qname.localName` with fallback. `_extract_period_info()` uses `context.isStartEndPeriod` / `context.isInstantPeriod` directly.
- **Tests:** 7 positive-path tests using mocks.

### 6. Converter/segmenter alignment for real filings (pipeline/converter.py, pipeline/segmenter.py)
- **Bug:** Segmenter only matched `## Item N` headings. Real SEC filings use `<div>`, `<p>`, `<b>` for Item headers.
- **Fix:** Converter detects and promotes SEC Item headers in `<p>`, `<b>`, `<div>` tags to `## ` headings. Segmenter gained a fallback pattern for plain-text Item lines.
- **Tests:** 6 new tests + new fixture `sample_html_filing_div_headers.html`.

### 7. Parser keywords too broad (intent_parser.py)
- **Bug:** `"content"`, `"markdown"`, `"full text"` in `filing_markdown` and `"facts"` in `company_facts` caused false routing.
- **Fix:** Removed generic keywords. Changed scoring from count-based to length-weighted (prefers longer, more specific matches). Added `"prices"` to `quote` intent. Added `S`, `P`, `PCE`, `FRED` to ticker stop words.
- **Tests:** Corpus accuracy remains 100%.

### 8. Benchmarks too shallow
- **Expanded:** Query corpus from 20 → 66 queries covering all intents plus edge cases.
- **Added:** 2 new HTML fixtures (`sample_html_filing_div_headers.html`, `sample_html_filing_table_toc.html`).
- **Added:** 10 new benchmark tests for larger fixture and div-header fixture.

## New ADRs
- `006-length-weighted-intent-scoring.md`
- `007-converter-item-header-promotion.md`
- `008-arelle-api-alignment.md`
- `009-llm-assisted-metadata-surfacing.md`
