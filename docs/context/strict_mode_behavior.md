# Strict Mode Behavior

## CLI Semantics

### `--strict` flag
When `--strict` is passed as a global option:
1. The orchestrator runs validation on all results
2. If any validation check FAILS, the response is marked `success=False`
3. The CLI suppresses data output (nothing to stdout)
4. Errors and validation summary are printed to stderr
5. Exit code is nonzero (1)

### Without `--strict`
1. Validation still runs but failures are treated as informational
2. Data is printed to stdout even if validation found issues
3. Exit code is 0 if data was retrieved, regardless of validation status

## Exit Codes
| Scenario | Exit Code | Stdout | Stderr |
|---|---|---|---|
| Success, validation passed | 0 | Formatted data | — |
| Success, validation warned (no strict) | 0 | Formatted data | — |
| Success, validation failed (no strict) | 0 | Formatted data | — |
| Strict mode, validation failed | 1 | Empty | Errors + validation summary |
| Retrieval failed (any mode) | 1 | Empty | Error messages |
| Missing required config | 1 | Empty | Config error |

## Orchestrator Semantics
- `_fetch_and_validate()` sets `success=False` only when `self._strict` is True AND validation fails
- Non-strict mode: `success` reflects retrieval success only
- Validation results are always attached to the response regardless of strict mode

## Testing
- `test_strict_failure_exits_nonzero` — verifies exit code
- `test_strict_failure_does_not_print_data` — verifies output suppression
- `test_success_prints_output` — verifies normal behavior
