# Runbook: Updating Docs Without Drift

## After Every Code Change

### 1. Check Version Consistency
```bash
grep -rn "version" pyproject.toml src/zion_terminal/__init__.py | head -5
```
Both must show the same version string.

### 2. Check README Config Against Code
```bash
grep -n "OLLAMA\|CACHE_TTL\|LLM_PROVIDER" README.md .env.example src/zion_terminal/config/settings.py
```
All three files must agree on variable names and default values.

### 3. Check CLI Commands Against README
```bash
python -c "from zion_terminal.cli import main; import click; print([c.name for c in main.commands.values()])"
```
Compare against the "Quick Start" section in README.md.

### 4. Check Architecture Doc Against Module Structure
```bash
find src/zion_terminal -name "*.py" -not -name "__init__*" | sort
```
Compare against `docs/architecture/overview.md`.

### 5. Run Doc-Checking Tests
```bash
pytest tests/unit/test_cli.py::TestCLI::test_version -x
pytest tests/unit/test_packaging.py -x
pytest tests/unit/test_cli.py::TestCLI::test_all_subcommands_exist -x
pytest tests/unit/test_filing_pipeline.py::TestFilingPipeline::test_pipeline_is_the_only_converter_path -x
```

### 6. Update Status Files
- `docs/status/changelog.md` — add entry
- `docs/status/implementation_status.md` — update table
- `docs/context/project-status.md` — update "What Works" / "What's Missing"
