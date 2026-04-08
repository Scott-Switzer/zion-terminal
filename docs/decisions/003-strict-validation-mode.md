# ADR-003: Strict Validation Mode

## Status
Accepted (v0.3.0)

## Context
In v0.2.0, validation runs but the results are advisory — invalid data is
still returned to the user with `success=True`. For applications that need
data quality guarantees (e.g. automated trading, compliance reporting),
there's no way to gate on validation results.

## Decision
Add `strict: bool` parameter to `Orchestrator.__init__()`.

When strict mode is enabled:
- If validation fails, `OrchestratorResponse.success` is set to `False`
- The data is still included (caller can inspect it)
- A warning is logged explaining the failure

CLI gets a global `--strict` flag that propagates to the orchestrator.

## Consequences
- Downstream consumers can trust `success=True` means validated data
- Non-strict mode (default) preserves v0.2.0 behavior
- Synthesis validation also respects strict mode
