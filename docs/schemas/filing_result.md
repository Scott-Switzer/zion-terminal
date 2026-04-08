# Schema: FilingPipelineResult

`FilingPipelineResult` is a dataclass defined in `src/zion_terminal/pipeline/filing_pipeline.py`. It is the canonical output of `FilingPipeline.process()`.

---

## Fields

| Field | Type | Description |
|---|---|---|
| `success` | `bool` | Whether the full pipeline completed without a fatal error. |
| `ticker` | `str` | Equity ticker symbol (e.g. `"AAPL"`). |
| `form` | `str` | SEC form type requested (e.g. `"10-K"`, `"10-Q"`, `"8-K"`). |
| `filing_date` | `str \| None` | ISO-8601 date string of the filing (`"2024-09-28"`), or `None` if not resolved. |
| `raw_html` | `str \| None` | Raw HTML content fetched from SEC EDGAR, before conversion. `None` if fetch failed. |
| `raw_char_count` | `int` | Character count of `raw_html`. `0` if HTML not available. |
| `markdown` | `str \| None` | Converted markdown output. `None` if conversion failed. |
| `markdown_char_count` | `int` | Character count of `markdown`. `0` if markdown not available. |
| `converter_engine` | `str` | Name of the converter used (e.g. `"markdownify"`, `"html2text"`, `"custom"`). |
| `sections` | `list[dict]` | List of extracted section dicts, each with `{"heading": str, "level": int, "char_count": int}`. |
| `section_count` | `int` | `len(sections)`. Convenience field. |
| `verification` | `dict \| None` | Nested verification result. See sub-schema below. `None` if verification was skipped. |
| `pipeline_metadata` | `dict` | Timing, adapter info, and cache metadata. See sub-schema below. |
| `errors` | `list[str]` | List of error messages from any pipeline stage. Empty list if none. |
| `warnings` | `list[str]` | Non-fatal warnings raised during conversion or verification. |

---

## Sub-schema: `verification`

When present, the `verification` dict has the structure:

```json
{
  "structural": {
    "success": true,
    "checks": ["has_content", "has_sections", "expected_sections_present"],
    "missing_sections": [],
    "section_count": 14
  },
  "xbrl": {
    "status": "unavailable",
    "reason": "Arelle not installed"
  },
  "cross_source": {
    "status": "skipped",
    "reason": "No secondary source configured"
  }
}
```

`xbrl.status` values: `"passed"` | `"failed"` | `"unavailable"` | `"skipped"`

---

## Sub-schema: `pipeline_metadata`

```json
{
  "fetch_duration_ms": 412,
  "convert_duration_ms": 87,
  "segment_duration_ms": 22,
  "verify_duration_ms": 14,
  "total_duration_ms": 535,
  "adapter": "sec_edgar",
  "cached": false,
  "cache_key": "sec_edgar:AAPL:10-K:latest"
}
```

---

## Example: Successful Result

```python
FilingPipelineResult(
    success=True,
    ticker="AAPL",
    form="10-K",
    filing_date="2024-09-28",
    raw_html="<html>...</html>",
    raw_char_count=1482300,
    markdown="# Apple Inc.\n\n## Part I\n\n...",
    markdown_char_count=312450,
    converter_engine="markdownify",
    sections=[
        {"heading": "Part I", "level": 1, "char_count": 42100},
        {"heading": "Item 1. Business", "level": 2, "char_count": 18200},
        {"heading": "Item 1A. Risk Factors", "level": 2, "char_count": 24300},
    ],
    section_count=14,
    verification={
        "structural": {
            "success": True,
            "checks": ["has_content", "has_sections", "expected_sections_present"],
            "missing_sections": [],
            "section_count": 14
        },
        "xbrl": {"status": "unavailable", "reason": "Arelle not installed"},
        "cross_source": {"status": "skipped", "reason": "No secondary source configured"}
    },
    pipeline_metadata={
        "fetch_duration_ms": 412,
        "convert_duration_ms": 87,
        "segment_duration_ms": 22,
        "verify_duration_ms": 14,
        "total_duration_ms": 535,
        "adapter": "sec_edgar",
        "cached": False,
        "cache_key": "sec_edgar:AAPL:10-K:latest"
    },
    errors=[],
    warnings=["Section 'Item 9A' matched via fuzzy heuristic"]
)
```

---

## Example: Failed Result

```python
FilingPipelineResult(
    success=False,
    ticker="ZZZZ",
    form="10-K",
    filing_date=None,
    raw_html=None,
    raw_char_count=0,
    markdown=None,
    markdown_char_count=0,
    converter_engine="none",
    sections=[],
    section_count=0,
    verification=None,
    pipeline_metadata={
        "fetch_duration_ms": 201,
        "total_duration_ms": 201,
        "adapter": "sec_edgar",
        "cached": False,
        "cache_key": "sec_edgar:ZZZZ:10-K:latest"
    },
    errors=["CIK lookup failed: ticker 'ZZZZ' not found in SEC EDGAR"],
    warnings=[]
)
```

---

## Notes

- `raw_html` is not persisted to cache by default — only `markdown` and `sections` are cached.
- `section_count` is always redundant with `len(sections)`. Both are present for convenience.
- `errors` is non-empty whenever `success=False`, but `success=False` can also occur with partial data (e.g. markdown was produced but verification failed fatally).
- The `converter_engine` field is set to `"none"` when conversion did not run.
