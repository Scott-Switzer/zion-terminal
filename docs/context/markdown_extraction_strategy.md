# Markdown Extraction Strategy

This document describes how numeric values are extracted from markdown documents produced by the Zion Terminal filing pipeline. The extraction logic lives in `src/zion_terminal/verification/markdown_extractor.py`.

---

## Overview

After a SEC filing is converted from HTML to markdown by `FilingPipeline`, the verification pipeline needs to compare the numeric values in that markdown against XBRL ground truth. This requires parsing the markdown to identify and normalize financial figures.

---

## Core Functions

### `parse_numeric(text: str) -> float | None`

Converts a string representation of a financial figure to a Python `float`.

**Supported formats:**

| Input | Output | Notes |
|---|---|---|
| `"42"` | `42.0` | Bare integer |
| `"3.14"` | `3.14` | Bare float |
| `"$213.49"` | `213.49` | Dollar prefix stripped |
| `"391,035"` | `391035.0` | Comma separators removed |
| `"$391,035M"` | `391035000000.0` | Millions suffix → actual dollars |
| `"$1.2B"` | `1200000000.0` | Billions suffix → actual dollars |
| `"$4.2K"` | `4200.0` | Thousands suffix → actual dollars |
| `"(4,200)"` | `-4200.0` | Parenthetical negative |
| `"(1.2B)"` | `-1200000000.0` | Parenthetical negative with billions |
| `"12.4%"` | `0.124` | Percentage → decimal fraction |
| `"—"` | `None` | Em dash (no data) |
| `"N/A"` | `None` | Not applicable |
| `""` | `None` | Empty string |

**Tolerance:** The XBRL reconciler uses a relative tolerance of 0.1% for `matched` status, accommodating minor rounding differences between source and representation.

---

### `extract_tables(markdown: str) -> list[dict]`

Scans a markdown string for pipe-table syntax and returns a list of table structures.

**Input:**
```markdown
| | FY2023 | FY2022 |
|---|---|---|
| Revenues | $391,035M | $394,328M |
| Net income | $96,995M | $99,803M |
```

**Output:**
```python
[
    {
        "headers": ["", "FY2023", "FY2022"],
        "rows": [
            ["Revenues", "$391,035M", "$394,328M"],
            ["Net income", "$96,995M", "$99,803M"],
        ]
    }
]
```

**Implementation notes:**
- Identifies tables by the presence of `|---|` separator rows.
- Strips leading/trailing whitespace from each cell.
- Does not validate table structure — malformed tables may produce unexpected results.
- Multiple tables in the same document are returned in document order.

---

### `extract_values(markdown: str) -> list[dict]`

The primary entry point. Combines `extract_tables` and `parse_numeric` to produce a flat list of label-value pairs.

**Algorithm:**
1. Call `extract_tables(markdown)`.
2. For each table, treat column 0 as the row label.
3. For each row, attempt `parse_numeric` on column 1 (the first data column).
4. If successful, emit `{"label": str, "value": float, "raw": str}`.
5. Skip rows where the label is empty or numeric (header/separator rows).

**Output:**
```python
[
    {"label": "Revenues", "value": 391035000000.0, "raw": "$391,035M"},
    {"label": "Net income", "value": 96995000000.0, "raw": "$96,995M"},
]
```

---

## Known Limitations

### 1. Tables only — no inline text
`extract_values` extracts numbers only from markdown pipe-tables. Inline text like "Revenue grew to $391 billion in fiscal 2023" is not parsed. This means any financial figures that appear in prose paragraphs (common in MD&A sections) are missed.

**Impact on reconciliation:** Some XBRL concepts may appear as `missing` in reconciliation results even though the value is present in the document — just not in a table.

### 2. First numeric column only
Only column 1 (the first data column) is parsed per row. Multi-year tables (e.g. FY2023, FY2022, FY2021) are partially parsed — only the FY2023 column is used.

**Impact:** Cross-year consistency checks are not possible with the current extractor.

### 3. No period inference
The extractor does not know which reporting period a value belongs to. Two rows labelled "Revenues" from different tables (e.g. annual vs. quarterly) cannot be disambiguated.

**Workaround:** The reconciler uses fuzzy label matching and accepts the closest match. This can produce false positives.

### 4. Scale detection is heuristic
The M/B/K suffixes are interpreted as millions/billions/thousands. Some filings express figures "in millions" in a table caption, with no suffix on individual cells. These values will not be correctly scaled.

**Impact:** `scale_mismatch` status is expected to be common for filings that use table-level scale notes.

---

## Future Improvements

- Parse inline text numerics using regex + NLP.
- Support multi-column extraction (per-year values).
- Detect table-level scale notes (e.g. "in millions") and apply them to all rows.
- Infer the reporting period from surrounding headings or table captions.

---

## Reference

- Module: `src/zion_terminal/verification/markdown_extractor.py`
- Consumer: `src/zion_terminal/verification/reconciler.py`
- Context: `docs/context/xbrl_reconciliation_design.md`
- ADR: `docs/decisions/ADR-0017-xbrl-ground-truth-reconciliation.md`
