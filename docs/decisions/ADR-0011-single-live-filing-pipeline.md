# ADR-0011: Single Live Filing Pipeline

## Status
Accepted

## Context
The SEC adapter had its own private `_html_to_markdown()` function (~120 lines) that duplicated the logic in `pipeline/converter.py`. Two conversion paths meant bugs could be fixed in one but not the other.

## Decision
All filing conversion flows through `pipeline/filing_pipeline.FilingPipeline`. The SEC adapter's private converter was deleted. A regression test ensures it cannot be reintroduced.

## Consequences
- One conversion path to maintain and test
- Filing pipeline results include structured metadata (conversion engine, segmentation, verification)
- The SEC adapter is simpler — it fetches HTML and delegates to the pipeline
- Any converter improvements automatically benefit all filing paths
