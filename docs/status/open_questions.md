# Open Questions

## Architecture
1. Should cross-source reconciliation be automatic or opt-in?
2. How should wrapper filings be detected and resolved?
3. Should the pipeline support iXBRL inline documents natively?
4. What is the right granularity for XBRL fact-to-markdown comparison?

## Data Quality
1. How to handle the ~10% of 10-Ks with non-standard formatting?
2. What accuracy threshold justifies marking a conversion as "reliable"?
3. How to distinguish TOC entries from real section headers in edge cases?

## Integration
1. How should Bloomberg ground truth data be used for validation (without publishing it)?
2. What is the right API surface for programmatic consumers?
3. Should verification results be exposed in the CLI output by default?

## Infrastructure
1. When should CI/CD be set up?
2. What is the deployment strategy for on-premises clients?
3. Should the cache schema version be bumped for v0.4.0?
