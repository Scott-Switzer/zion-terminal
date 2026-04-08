# Open Questions

## Architecture
1. Should the NL intent parser become SEC-aware for financial queries?
2. Should cross-source reconciliation be automatic or opt-in?
3. How should wrapper filings be detected and resolved?
4. Should the pipeline support iXBRL inline documents natively?
5. What is the right granularity for XBRL fact-to-markdown comparison?

## Data Quality
1. How to handle the ~10% of 10-Ks with non-standard formatting?
2. What accuracy threshold justifies marking a conversion as "reliable"?
3. How to distinguish TOC entries from real section headers in edge cases?
4. Should the pipeline warn when a filing appears to be a wrapper?

## Validation
1. What heuristics should drive automated cross-source reconciliation?
2. How to handle cases where SEC and Yahoo disagree on key values?
3. Should strict mode suppress ALL output or only the data portion?
4. How should verification results be exposed in JSON output?

## Testing
1. How to add real (anonymized) SEC filing fragments as benchmarks?
2. Should integration tests run in CI against live APIs?
3. What is the right size for the parser accuracy corpus?
4. Should benchmarks track performance trends across versions?

## Infrastructure
1. When should CI/CD be set up?
2. What is the deployment strategy for on-premises clients?
3. Should the cache schema version be bumped for v0.4.x?
