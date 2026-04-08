# XBRL Reconciliation Design

This document describes the XBRL-to-markdown reconciliation approach used in the Zion Terminal verification pipeline.

---

## Goal

Given a filing that has been:
1. Fetched as HTML from SEC EDGAR
2. Converted to markdown by `FilingPipeline`

The reconciler answers: **do the numeric values in the markdown match the XBRL-reported facts?**

This catches conversion errors where numbers are dropped, scaled incorrectly, or have their sign flipped.

---

## Architecture

Three modules implement reconciliation, all in `src/zion_terminal/verification/`:

```
verification/
├── fact_mapping.py        # Arelle → canonical fact schema
├── markdown_extractor.py  # Markdown tables → numeric values
└── reconciler.py          # Comparison and match status
```

---

## fact_mapping.py

**Purpose:** Extract XBRL facts from Arelle and normalize them to a canonical schema.

**Canonical fact schema:**

```python
@dataclass
class CanonicalFact:
    concept: str          # e.g. "us-gaap/Revenues"
    label: str            # e.g. "Revenues"
    value: float          # unscaled (actual dollars)
    unit: str             # e.g. "USD", "shares"
    period: str           # ISO date or range: "2023-09-30" or "2022-10-01/2023-09-30"
    form: str             # "10-K", "10-Q", etc.
    decimals: int | None  # Arelle decimals attribute (-6 = millions, -3 = thousands)
```

**Key logic:**
- Uses `model.facts` to iterate Arelle facts.
- Filters to the most recent period for each concept.
- Applies `fact.xValue` to get the typed Python value (avoids string parsing).
- Normalizes unit from `fact.unit.measures`.

**Dependency:** Arelle must be installed (`pip install arelle-release`). When absent, `fact_mapping.py` raises `ImportError` and the caller marks the check as `"unavailable"`.

---

## markdown_extractor.py

**Purpose:** Extract numeric values from a markdown document (output of `FilingPipeline`).

**Key functions:**

### `parse_numeric(text: str) -> float | None`

Parses a single string value into a float. Handles:

- Bare numbers: `"42"` → `42.0`
- Dollar amounts: `"$213.49"` → `213.49`
- Millions suffix: `"$391,035M"` → `391035000000.0` (converted to actual dollars)
- Billions suffix: `"$1.2B"` → `1200000000.0`
- Parenthetical negatives: `"(4,200)"` → `-4200.0`
- Percentages: `"12.4%"` → `0.124`

Returns `None` if the string cannot be parsed as a numeric value.

### `extract_tables(markdown: str) -> list[dict]`

Parses all markdown table syntax blocks. Returns a list of dicts:

```python
[
    {
        "headers": ["", "2023", "2022"],
        "rows": [
            ["Revenues", "$391,035M", "$394,328M"],
            ["Net income", "$96,995M", "$99,803M"],
        ]
    }
]
```

### `extract_values(markdown: str) -> list[dict]`

Combines `extract_tables` and `parse_numeric`. For each row in each table, attempts to parse the first numeric column as a float:

```python
[
    {"label": "Revenues", "value": 391035000000.0, "raw": "$391,035M"},
    {"label": "Net income", "value": 96995000000.0, "raw": "$96,995M"},
]
```

**Limitations** (see also `docs/context/markdown_extraction_strategy.md`):
- Only extracts from markdown tables, not inline text.
- Only parses the first numeric column per row.
- No period inference — does not know which year a value belongs to.

---

## reconciler.py

**Purpose:** Compare XBRL canonical facts against markdown-extracted values and produce match statuses.

**Match statuses:**

| Status | Meaning |
|---|---|
| `matched` | XBRL value and markdown value are equal (within 0.1% relative tolerance). |
| `scale_mismatch` | Values differ by exactly 1000× or 1,000,000× — likely a millions/thousands scaling error. |
| `sign_mismatch` | Values are equal in magnitude but opposite in sign — negation error. |
| `label_mismatch` | The concept label was found in the markdown, but the numeric values do not match. |
| `missing` | The XBRL concept was not found in the markdown at all. |

**Algorithm:**

```
for each canonical_fact in xbrl_facts:
    candidates = extract_values(markdown) where label fuzzy-matches fact.label
    if no candidates:
        status = "missing"
    elif any candidate within tolerance:
        status = "matched"
    elif any candidate differs by scale factor:
        status = "scale_mismatch"
    elif any candidate has opposite sign:
        status = "sign_mismatch"
    else:
        status = "label_mismatch"
```

**Output:**

```python
@dataclass
class ReconciliationResult:
    total_facts: int
    matched: int
    scale_mismatch: int
    sign_mismatch: int
    label_mismatch: int
    missing: int
    match_rate: float           # matched / total_facts
    details: list[FactMatch]    # per-fact results
```

---

## Integration Status

**Implemented:**
- `fact_mapping.py` — fact extraction and canonical schema.
- `markdown_extractor.py` — numeric value extraction from markdown tables.
- `reconciler.py` — comparison logic and match status assignment.

**Pending:**
- End-to-end integration into `FilingPipeline.process()` (currently the reconciler is called in tests, not in the live pipeline).
- Integration into `ValidationAgent` as a `ValidationCheck`.
- Arelle installation in CI environment.
- Benchmark runner integration (reconciliation metrics not yet included in `latest.json`).

---

## Reference

- ADR: `docs/decisions/ADR-0017-xbrl-ground-truth-reconciliation.md`
- Context: `docs/context/arelle_live_validation.md`
- Context: `docs/context/markdown_extraction_strategy.md`
- Schema: `docs/schemas/validation_result.md`
