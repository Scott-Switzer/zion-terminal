# Arelle XBRL Verification Profile

## What This Is

The Arelle verification profile is a stricter test/verification mode that
requires the `arelle-release` package. It validates XBRL instance documents
using the XBRL International-certified Arelle processor.

## When To Use It

- In CI environments that verify XBRL correctness
- When validating against real SEC filing instance documents
- When the full verification story is required (not just company-facts reconciliation)

## How To Run It

```bash
# Install Arelle
pip install arelle-release

# Run the Arelle verification tests
python -m pytest tests/unit/test_arelle_xbrl.py -v

# Run all tests including Arelle profile
python -m pytest tests/ -v
```

## What Happens Without Arelle

- Tests in `test_arelle_xbrl.py` are **skipped** (not failed)
- Company-facts reconciliation **still runs** (does not require Arelle)
- Verification status reports `no_xbrl_url` for the Arelle tier
- The skip message explicitly says "XBRL verification profile NOT satisfied"

## What the Arelle Tests Verify

| Test | What It Proves |
|------|---------------|
| `test_arelle_is_available` | Arelle package loads correctly |
| `test_verifier_reports_available` | XBRLVerifier detects Arelle |
| `test_extract_concept_name_handles_none` | Fact extraction handles edge cases |
| `test_extract_unit_handles_none` | Unit extraction handles edge cases |
| `test_invalid_url_returns_error` | Invalid documents produce error results, not crashes |
| `test_empty_string_returns_error` | Empty input produces error results |

## CI Enforcement

To enforce the Arelle profile in CI, add this step:

```yaml
- name: XBRL Verification Profile
  run: |
    pip install arelle-release
    python -m pytest tests/unit/test_arelle_xbrl.py -v --strict-markers
```

If this step fails or skips, the XBRL verification profile is not satisfied.
