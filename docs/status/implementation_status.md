# Implementation Status

## Implemented (v0.4.1)

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

## Not Yet Implemented

| Feature | Priority | Notes |
|---|---|---|
| Parser SEC-awareness | Medium | NL parser still assigns yahoo_finance to some financial queries |
| Automated cross-source reconciliation | Medium | Hooks exist, comparison logic not implemented |
| Wrapper filing detection | Medium | Fixture exists, detection not automated |
| iXBRL inline parsing | Low | Arelle supports it, not wired |
| Historical filing comparison | Low | Compare across multiple years |
| TOC vs section discrimination | Medium | Heuristic improvement needed |
| CI/CD pipeline | Medium | Not yet set up |
| mypy type checking | Low | Not enforced |
| Real SEC filing benchmarks | Medium | Only synthetic fixtures |
