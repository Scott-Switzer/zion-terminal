# Schema: ValidationResult

`ValidationResult` is a Pydantic model defined in `src/zion_terminal/agents/validation/models.py`. It is the return type of `ValidationAgent.validate()`.

---

## Top-level Fields

| Field | Type | Description |
|---|---|---|
| `agent` | `str` | Name of the agent that ran validation. Always `"validation"`. |
| `success` | `bool` | `True` if all checks passed or only warnings were raised. `False` if any check failed. |
| `status` | `Literal["passed", "failed", "warning"]` | Overall status. `"warning"` means checks ran but some produced non-fatal issues. |
| `checks_run` | `int` | Total number of checks attempted. |
| `checks_passed` | `int` | Number of checks with status `"passed"`. |
| `checks_warned` | `int` | Number of checks with status `"warning"`. |
| `checks_failed` | `int` | Number of checks with status `"failed"`. |
| `checks_skipped` | `int` | Number of checks skipped (preconditions not met, e.g. data not available). |
| `checks_unavailable` | `int` | Number of checks that could not run due to missing dependencies (e.g. Arelle not installed). |
| `details` | `list[ValidationCheck]` | Per-check detail records. See sub-schema below. |
| `warnings` | `list[str]` | Free-text warning messages not associated with a specific check. |

### Status Semantics

- `"passed"` — `checks_failed == 0` and `checks_warned == 0`
- `"warning"` — `checks_failed == 0` and `checks_warned > 0`
- `"failed"` — `checks_failed > 0`

`success` is `True` when status is `"passed"` or `"warning"`.

---

## Sub-schema: ValidationCheck

Each entry in `details` is a `ValidationCheck` model:

| Field | Type | Description |
|---|---|---|
| `check_name` | `str` | Identifier for the check (e.g. `"has_sections"`, `"revenue_cross_check"`). |
| `status` | `Literal["passed", "failed", "warning", "skipped", "unavailable"]` | Result of this individual check. |
| `message` | `str` | Human-readable description of the check result. |
| `field` | `str \| None` | The data field being checked (e.g. `"section_count"`, `"revenue"`). `None` for structural checks. |
| `expected` | `Any \| None` | Expected value or constraint. `None` if not applicable. |
| `actual` | `Any \| None` | Actual observed value. `None` if unavailable. |

---

## Example: Full Passed Result

```json
{
  "agent": "validation",
  "success": true,
  "status": "passed",
  "checks_run": 6,
  "checks_passed": 5,
  "checks_warned": 0,
  "checks_failed": 0,
  "checks_skipped": 0,
  "checks_unavailable": 1,
  "details": [
    {
      "check_name": "has_content",
      "status": "passed",
      "message": "Markdown content is non-empty (312450 chars)",
      "field": "markdown_char_count",
      "expected": "> 0",
      "actual": 312450
    },
    {
      "check_name": "has_sections",
      "status": "passed",
      "message": "14 sections extracted",
      "field": "section_count",
      "expected": ">= 3",
      "actual": 14
    },
    {
      "check_name": "expected_sections_present",
      "status": "passed",
      "message": "All expected 10-K sections found",
      "field": "sections",
      "expected": ["Part I", "Part II", "Part III"],
      "actual": ["Part I", "Part II", "Part III", "Part IV"]
    },
    {
      "check_name": "no_conversion_errors",
      "status": "passed",
      "message": "No conversion errors reported",
      "field": "errors",
      "expected": "[]",
      "actual": "[]"
    },
    {
      "check_name": "section_size_sanity",
      "status": "passed",
      "message": "All sections have non-zero character counts",
      "field": "sections",
      "expected": "all > 0",
      "actual": "all > 0"
    },
    {
      "check_name": "xbrl_reconciliation",
      "status": "unavailable",
      "message": "Arelle not installed — XBRL reconciliation skipped",
      "field": null,
      "expected": null,
      "actual": null
    }
  ],
  "warnings": []
}
```

---

## Example: Failed Result

```json
{
  "agent": "validation",
  "success": false,
  "status": "failed",
  "checks_run": 4,
  "checks_passed": 2,
  "checks_warned": 0,
  "checks_failed": 1,
  "checks_skipped": 1,
  "checks_unavailable": 0,
  "details": [
    {
      "check_name": "has_content",
      "status": "passed",
      "message": "Markdown content is non-empty",
      "field": "markdown_char_count",
      "expected": "> 0",
      "actual": 4200
    },
    {
      "check_name": "has_sections",
      "status": "failed",
      "message": "Only 1 section extracted — minimum is 3",
      "field": "section_count",
      "expected": ">= 3",
      "actual": 1
    },
    {
      "check_name": "expected_sections_present",
      "status": "skipped",
      "message": "Skipped — section_count check failed",
      "field": "sections",
      "expected": null,
      "actual": null
    },
    {
      "check_name": "no_conversion_errors",
      "status": "passed",
      "message": "No conversion errors reported",
      "field": "errors",
      "expected": "[]",
      "actual": "[]"
    }
  ],
  "warnings": ["Filing may be a wrapper document referencing an exhibit"]
}
```

---

## Notes

- `checks_run` = `checks_passed` + `checks_warned` + `checks_failed` + `checks_skipped` + `checks_unavailable`
- `checks_skipped` differs from `checks_unavailable`: skipped means a precondition was not met (e.g. upstream check failed); unavailable means a dependency is missing.
- The `ValidationAgent` runs checks in two phases: retrieval validation and synthesis validation. `details` covers both.
- This model is serializable to JSON via `.model_dump()` (Pydantic v2) or `.dict()` (Pydantic v1).
