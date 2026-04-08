# Documentation Consistency Rules

## Principle
Docs must describe the actual code, not aspirational features.

## After Every Major Change
1. Update `docs/context/project-status.md` with current capabilities
2. Update `docs/architecture/overview.md` if any architectural change
3. Check README.md config examples against `.env.example` and `settings.py`
4. Check CLI command list in README against actual registered click commands
5. Update version in both `pyproject.toml` and `__init__.py`

## Claims That Must Match Code

| Claim Location | Must Match |
|---|---|
| README "Configuration" section | `.env.example` variable names and `settings.py` defaults |
| README "Quick Start" commands | Registered click commands in `cli.py` |
| README version | `pyproject.toml` and `__init__.py` version |
| README "Supported data sources" | Registered adapters in `agents/retrieval/agent.py` |
| docs/architecture/overview.md | Actual module structure in `src/zion_terminal/` |
| ADR files | Actual code implementing the decision |

## Automated Checks
- `test_version` in `test_cli.py` checks CLI version matches `__init__.py`
- `test_import_root` in `test_packaging.py` checks `__version__`
- `test_all_subcommands_exist` in `test_cli.py` checks help output
- `test_pipeline_is_the_only_converter_path` in `test_filing_pipeline.py` checks no duplicate converter

## Process
After finishing a code change:
1. `grep -r "0.X.Y" docs/ README.md pyproject.toml src/zion_terminal/__init__.py` — version consistency
2. Review each docs file touched in the commit
3. Run the test suite — doc-checking tests will catch obvious drift
