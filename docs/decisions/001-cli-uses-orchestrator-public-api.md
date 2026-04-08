# ADR-001: CLI Must Use Orchestrator Public API

## Status
Accepted (v0.3.0)

## Context
In v0.2.0, CLI commands like `history`, `financials`, and `filings` bypassed
the orchestrator and called `orc._retrieval.fetch()` directly. This meant:

1. Validation was skipped for direct CLI commands
2. CLI was coupled to internal agent structure
3. No way to enable strict mode for CLI operations

## Decision
Add public methods to `Orchestrator`:
- `get_quote()`, `get_history()`, `get_financials()`, `get_filings()`
- `get_macro()`, `get_info()`, `get_filing_markdown()`, `get_company_facts()`

All public methods route through `_fetch_and_validate()` which applies
the validation pipeline and respects strict mode.

CLI commands must only call these public methods. A test
(`test_cli_does_not_access_private_agents`) enforces this.

## Consequences
- All data paths go through validation
- Strict mode works everywhere
- CLI is decoupled from internal agent structure
- Adding new data sources only requires an adapter + orchestrator method
