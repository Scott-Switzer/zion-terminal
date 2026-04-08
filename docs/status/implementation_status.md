# Implementation Status

## Implemented (v0.4.0)

| Feature | Status | Notes |
|---|---|---|
| SEC-first financial statements | Done | Default source is SEC EDGAR |
| Unified filing pipeline | Done | Single path: converter → segmenter → verifier |
| Filing pipeline result model | Done | Structured dataclass with metadata |
| Structural verification | Done | Content, sections, expected sections |
| XBRL/Arelle verification | Done | Optional dependency, graceful degradation |
| Cross-source hooks | Done (hooks) | Logic pending — hooks exist, reconciliation not automated |
| Source role model | Done | `SOURCE_ROLES` matrix in `source_roles.py` |
| Deterministic synthesis | Done | Seeded RNG, reproducible in no-LLM mode |
| Yahoo as verification layer | Done | Available via `--source yahoo` flag |
| Regression tests for pipeline | Done | Tests prevent reintroduction of duplicate converter |

## Not Yet Implemented

| Feature | Priority | Notes |
|---|---|---|
| Automated cross-source reconciliation | Medium | Hooks exist, logic not implemented |
| Wrapper filing detection | Medium | Known edge case, not yet handled |
| iXBRL inline parsing | Low | Arelle supports it, not wired |
| Historical filing comparison | Low | Compare across multiple years |
| TOC vs section discrimination | Medium | Heuristic improvement needed |
| CI/CD pipeline | Medium | Not yet set up |
| mypy type checking | Low | Not enforced |
