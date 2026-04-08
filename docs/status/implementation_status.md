# Implementation Status

## Implemented (v0.4.3)

| Feature | Status | Notes |
|---|---|---|
| SEC-first financial statements | Done | Default source is SEC; auto-router and convenience methods updated |
| Unified filing pipeline | Done | Single path through FilingPipeline; regression test prevents private converters |
| Filing pipeline result model | Done | Structured dataclass with conversion, segmentation, verification metadata |
| Structural verification | Done | Content, sections, expected sections for form type |
| XBRL/Arelle verification | Done | Optional dependency, graceful degradation |
| Cross-source hooks | Done (hooks) | Hooks exist; automated reconciliation not implemented |
| Source role model | Done | SOURCE_ROLES matrix in source_roles.py |
| Deterministic synthesis | Done | Seeded RNG, reproducible in no-LLM mode |
| Yahoo as verification layer | Done | Available via --source yahoo flag |
| Strict mode safety | Done | Invalid data suppressed in strict mode; errors to stderr |
| Company facts formatter | Done | Structured markdown with header, metadata, sample table |
| Auto-routing for financials | Done | _find_adapter_for_task routes financials to SEC |
| Live pipeline benchmarks | Done | FilingPipeline.process() benchmarked alongside converter |
| Dead code removal | Done | No unused imports or stale conversion paths |
| TOC discrimination | Done | Filters dense Item clusters in first 20% as TOC entries |
| Synthesis regeneration scaffold | Done | generate_with_retry() retries with perturbed seeds on validation failure |
| Extended validation schema | Done | checks_warned, checks_skipped, checks_unavailable fields |
| XBRL fact mapping module | Done | verification/fact_mapping.py — Arelle → canonical CanonicalFact schema |
| Markdown numeric extractor | Done | verification/markdown_extractor.py — parse_numeric, extract_tables, extract_values |
| XBRL reconciler module | Done | verification/reconciler.py — matched/scale_mismatch/sign_mismatch/label_mismatch/missing |
| Parser corpus expansion | Done | 110+ queries across 10 categories (up from 66) |
| Benchmark runner script | Done | scripts/run_benchmarks.py |
| Doc consistency checker | Done | scripts/check_doc_consistency.py |

## Not Yet Implemented

| Feature | Priority | Notes |
|---|---|---|
| Parser SEC-awareness | Medium | NL parser still assigns yahoo_finance to some financial queries |
| Automated cross-source reconciliation | Medium | Hooks exist, comparison logic not wired into live pipeline |
| Wrapper filing detection | Medium | Fixture exists, detection not automated |
| iXBRL inline parsing | Low | Arelle supports it, not wired |
| Historical filing comparison | Low | Compare across multiple years |
| XBRL reconciliation in live pipeline | High | fact_mapping + reconciler modules exist; FilingPipeline.process() integration pending |
| XBRL reconciliation in ValidationAgent | High | reconciler.py not yet called from ValidationAgent |
| Table fidelity metric | Medium | Benchmark metric defined in ADR-0016 but not implemented in runner |
| Numeric fidelity metric (end-to-end) | High | markdown_extractor.py implemented; benchmark runner integration pending |
| CI/CD pipeline | Medium | Not yet set up |
| Arelle in CI | Medium | Required for XBRL reconciliation metrics |
| mypy type checking | Low | Not enforced |
| Real SEC filing benchmarks | Medium | Only synthetic fixtures |
| Adversarial parser corpus | Low | No injection or adversarial queries in corpus |
| Multi-column table extraction | Low | markdown_extractor.py only parses first data column |
| Inline text numeric extraction | Low | Only table-based extraction implemented |
| Parser label matching improvement | Medium | Fuzzy label matching in reconciler.py can produce false positives |
