# Live Validation Results Schema

## ValidationResult (from agents/validation/agent.py)
```json
{
  "agent": "validation",
  "success": true,
  "status": "passed | failed | warning",
  "checks_run": 5,
  "checks_passed": 4,
  "checks_failed": 1,
  "details": [
    {
      "check_name": "price_bounds",
      "status": "passed | failed | warning",
      "message": "Price 185.50 within bounds",
      "field": "item[0].price",
      "expected": null,
      "actual": 185.50
    }
  ]
}
```

## VerificationResult (from pipeline/verification.py)
```json
{
  "status": "passed | failed | partial | unavailable | not_run",
  "xbrl_status": "passed | failed | unavailable | error | no_xbrl_url | not_run",
  "xbrl_facts_extracted": 0,
  "xbrl_errors": [],
  "cross_source_status": "checked | no_comparison_data | not_run",
  "cross_source_checks": [],
  "structural_checks": [
    {
      "check": "markdown_content",
      "status": "passed | failed | warning",
      "message": "Markdown content present (5000 chars)"
    }
  ],
  "warnings": []
}
```

## Status Taxonomy
| Status | Meaning |
|---|---|
| `passed` | All checks ran and passed |
| `failed` | At least one critical check failed |
| `warning` | Checks ran but found non-critical issues |
| `partial` | Some checks passed, some failed |
| `unavailable` | Required tool not installed (e.g., Arelle) |
| `skipped` | Check was intentionally skipped |
| `not_run` | Check has not been executed |
