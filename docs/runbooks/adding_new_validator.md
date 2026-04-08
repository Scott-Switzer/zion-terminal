# Runbook: Adding a New Validation Check

This runbook describes how to add new checks to the `ValidationAgent`.

---

## Overview

The `ValidationAgent` (in `src/zion_terminal/agents/validation/agent.py`) runs two phases of checks:

1. **Retrieval validation** (`validate_retrieval`) — checks the raw output from adapters (e.g. section count, content presence, structural integrity).
2. **Synthesis validation** (`validate_synthesis`) — checks the formatted output produced by the synthesis agent (e.g. numeric plausibility, cross-source agreement).

Each check produces a `ValidationCheck` record and contributes to the aggregate `ValidationResult`.

---

## Step 1: Define the Check

Decide which phase your check belongs in:

| Phase | Method | When to use |
|---|---|---|
| Structural | `validate_retrieval` | Checks on raw adapter output (sections, char counts, presence of expected fields) |
| Numeric | `validate_synthesis` | Checks on synthesized output or computed numeric values |
| Cross-source | `validate_synthesis` | Comparisons between SEC and Yahoo or other secondary sources |

Within the appropriate method, add a new `ValidationCheck` to the `checks` list:

```python
from zion_terminal.agents.validation.models import ValidationCheck

# Structural check example (in validate_retrieval):
checks.append(ValidationCheck(
    check_name="min_section_char_count",
    status="passed" if all(s["char_count"] > 100 for s in sections) else "failed",
    message=(
        "All sections have >= 100 chars"
        if all(s["char_count"] > 100 for s in sections)
        else f"Some sections are too short: {[s['heading'] for s in sections if s['char_count'] <= 100]}"
    ),
    field="sections",
    expected=">= 100 chars per section",
    actual=f"min={min((s['char_count'] for s in sections), default=0)}",
))
```

---

## Step 2: Use the ValidationCheck Model Correctly

`ValidationCheck` fields:

| Field | Required | Notes |
|---|---|---|
| `check_name` | Yes | Unique snake_case identifier. Use a descriptive name — this appears in JSON output. |
| `status` | Yes | One of: `"passed"`, `"failed"`, `"warning"`, `"skipped"`, `"unavailable"`. |
| `message` | Yes | Human-readable explanation. Should be specific, not generic. |
| `field` | No | The field being checked (e.g. `"section_count"`). Omit for checks that don't target a specific field. |
| `expected` | No | The expected value or constraint as a string. |
| `actual` | No | The observed value. Serialize complex objects to string. |

**Use `"skipped"` when** a precondition is not met (e.g. an upstream check failed or data was not available for this run).

**Use `"unavailable"` when** a required dependency is missing (e.g. Arelle not installed, secondary source not configured).

**Use `"warning"` when** the result is not ideal but should not block the pipeline.

---

## Step 3: Place in the Appropriate Section

Checks should be grouped logically within each phase method. Use a comment header to delimit sections:

```python
# --- Structural checks ---
# ... existing structural checks ...
checks.append(my_new_structural_check)

# --- Numeric checks ---
# ... existing numeric checks ...

# --- Cross-source checks ---
# ... existing cross-source checks ...
```

If adding a cross-source check, verify that the secondary source data is actually present before attempting the comparison. If it's absent, use `status="skipped"` with a descriptive message.

---

## Step 4: Write Fixture-based Tests

Create or extend test files in `tests/`:

```python
# tests/test_validation_agent.py

def test_min_section_char_count_passes():
    sections = [
        {"heading": "Part I", "level": 1, "char_count": 4200},
        {"heading": "Item 1", "level": 2, "char_count": 1800},
    ]
    result = ValidationAgent().validate_retrieval(
        data={"sections": sections, "markdown_char_count": 6000, "errors": []}
    )
    check = next(c for c in result.details if c.check_name == "min_section_char_count")
    assert check.status == "passed"


def test_min_section_char_count_fails():
    sections = [
        {"heading": "Part I", "level": 1, "char_count": 4200},
        {"heading": "Empty Section", "level": 2, "char_count": 10},
    ]
    result = ValidationAgent().validate_retrieval(
        data={"sections": sections, "markdown_char_count": 4210, "errors": []}
    )
    check = next(c for c in result.details if c.check_name == "min_section_char_count")
    assert check.status == "failed"
    assert "Empty Section" in check.message
```

**Use fixture data** (not live API calls) in unit tests. Live API checks should be in integration tests decorated with `@pytest.mark.integration`.

---

## Step 5: Update docs/context/verification_levels.md

Add an entry to `docs/context/verification_levels.md` describing the new check:

```markdown
### min_section_char_count (structural)
**Phase:** retrieval validation  
**When:** Always (when sections are present)  
**Passes when:** Every section has >= 100 characters.  
**Fails when:** Any section has < 100 characters.  
**Why:** Very short sections often indicate a broken segmentation or a TOC artifact.
```

---

## Reference

- ValidationAgent: `src/zion_terminal/agents/validation/agent.py`
- Models: `src/zion_terminal/agents/validation/models.py`
- Schema doc: `docs/schemas/validation_result.md`
- Context: `docs/context/verification_levels.md`
- Existing tests: `tests/test_validation_agent.py`
