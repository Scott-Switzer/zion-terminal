# Open Questions

## Architecture
1. Should the NL intent parser become SEC-aware for financial queries?
2. Should cross-source reconciliation be automatic or opt-in?
3. How should wrapper filings be detected and resolved?
4. Should the pipeline support iXBRL inline documents natively?
5. What is the right granularity for XBRL fact-to-markdown comparison?
6. Should `FilingPipeline.process()` call the XBRL reconciler by default, or only when explicitly requested?
7. Should the reconciler's match tolerance (currently 0.1%) be configurable per adapter or per form type?

## Data Quality
1. How to handle the ~10% of 10-Ks with non-standard formatting?
2. What accuracy threshold justifies marking a conversion as "reliable"?
3. How to distinguish TOC entries from real section headers in edge cases?
4. Should the pipeline warn when a filing appears to be a wrapper?
5. How should multi-column markdown tables be handled in `markdown_extractor.py` — extract all columns, or just the most recent period?
6. How should table-level scale notes (e.g. "in millions") be detected and applied to `parse_numeric`?

## Validation
1. What heuristics should drive automated cross-source reconciliation?
2. How to handle cases where SEC and Yahoo disagree on key values?
3. Should strict mode suppress ALL output or only the data portion?
4. How should verification results be exposed in JSON output?

## Benchmarking
1. Should the benchmark suite use real (anonymized) SEC filing HTML instead of synthetic fixtures? What is the legal/copyright risk of committing real filing HTML to the repository?
2. How should XBRL end-to-end integration be tested — with real filings, or with synthetic XBRL documents?
3. Should the parser corpus include adversarial/injection queries? If so, what is the expected parser behavior on injection attempts?
4. How should parser accuracy be measured against real user query distributions (not just self-authored synthetic corpus)?
5. Should the benchmark runner run in CI on every PR, or only on release branches?
6. When Arelle is unavailable, should the composite score be renormalized or flagged as incomplete?

## Testing
1. How to add real (anonymized) SEC filing fragments as benchmarks?
2. Should integration tests run in CI against live APIs?
3. What is the right size for the parser accuracy corpus?
4. Should benchmarks track performance trends across versions?
5. Should `tests/fixtures/` be split into unit-test fixtures and benchmark fixtures to avoid slow tests blocking fast feedback?

## Infrastructure
1. When should CI/CD be set up?
2. What is the deployment strategy for on-premises clients?
3. Should the cache schema version be bumped for v0.4.x?
4. Should Arelle be added as an optional CI dependency, and if so, how should the CI matrix handle runs with and without Arelle?
5. Should `scripts/run_benchmarks.py` write to a `docs/benchmarks/baseline.json` automatically, or should that be a manual step to prevent accidental baseline drift?
