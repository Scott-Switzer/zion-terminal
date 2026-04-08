# Live Filing Pipeline

## Architecture

All SEC filing conversion flows through a single pipeline:

```
SEC EDGAR API (edgartools)
    │
    ▼
Raw HTML acquisition
    │
    ▼
FilingPipeline.process()
    ├── Stage 1: FilingConverter (HTML → Markdown)
    │   ├── DOM-based (bs4/lxml) preferred
    │   ├── Regex fallback if bs4 unavailable
    │   └── SEC Item header promotion (div/p/b → ## headings)
    ├── Stage 2: FilingSegmenter (Markdown → Sections)
    │   ├── Heading-prefixed pattern (primary)
    │   └── Plain-text pattern (fallback)
    └── Stage 3: Verification hooks
        ├── Structural checks
        ├── XBRL/Arelle validation (optional)
        └── Cross-source comparison (optional)
    │
    ▼
FilingPipelineResult (structured)
    │
    ▼
OrchestratorResponse (to CLI/API)
```

## Key Invariant

There is ONE conversion path for filing HTML → markdown. The SEC adapter's former private `_html_to_markdown()` function has been removed. All conversion goes through `pipeline.filing_pipeline.FilingPipeline`.

A regression test (`test_pipeline_is_the_only_converter_path`) will fail if a private converter is reintroduced in the SEC adapter.

## Pipeline Module Location

- `src/zion_terminal/pipeline/filing_pipeline.py` — orchestrates the stages
- `src/zion_terminal/pipeline/converter.py` — HTML → markdown
- `src/zion_terminal/pipeline/segmenter.py` — markdown → sections
- `src/zion_terminal/pipeline/verification.py` — structural + XBRL + cross-source
- `src/zion_terminal/pipeline/xbrl.py` — Arelle wrapper

## FilingPipelineResult

The pipeline returns a structured `FilingPipelineResult` dataclass with:
- Conversion metadata (engine used, char counts, truncation)
- Segmentation metadata (sections found, their items/titles)
- Verification metadata (structural checks, XBRL status, cross-source status)
- Pipeline provenance (source role, source name)
