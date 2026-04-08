# Runbook: Adding a New Benchmark Fixture

Benchmark fixtures are static HTML files used to test the filing pipeline (HTML-to-markdown conversion, section segmentation, and structural verification) in a reproducible, offline way.

---

## Overview

Fixtures are stored in `tests/fixtures/`. Each fixture represents a realistic or edge-case HTML document. The benchmark suite processes each fixture through `FilingPipeline.process()` and checks the output against known-good expectations.

---

## Step 1: Create the HTML Fixture File

Place the HTML file in `tests/fixtures/`:

```bash
tests/fixtures/
├── sample_10k_simple.html          # minimal 10-K
├── sample_10k_large.html           # full-size 10-K
├── sample_10k_div_headers.html     # div-based headers (no <h1>/<h2>)
├── sample_10k_toc_heavy.html       # TOC-heavy document
├── sample_10k_wrapper.html         # wrapper filing referencing an exhibit
└── my_new_fixture.html             # YOUR NEW FIXTURE
```

**Naming convention:** `<form_type>_<distinguishing_feature>.html`

Example: `10q_nested_tables.html`, `10k_xbrl_inline.html`

**Content requirements:**
- Must be valid HTML (or at least parseable by `markdownify` / `html2text`).
- Should be representative of a real edge case or category of filings.
- Sensitive data should be anonymized or synthetic (no real CIK, no real company PII).
- Keep file size reasonable: small fixtures (< 50 KB) are preferred for fast test runs. Large fixtures (for performance benchmarks) should be gated with `@pytest.mark.slow`.

---

## Step 2: Create expected_sections.json (if applicable)

If you want to assert that specific sections are extracted correctly, create a companion JSON file:

```bash
tests/fixtures/my_new_fixture_expected_sections.json
```

Format:

```json
{
  "fixture": "my_new_fixture.html",
  "form": "10-K",
  "min_section_count": 8,
  "expected_headings": [
    "Part I",
    "Item 1. Business",
    "Item 1A. Risk Factors",
    "Part II",
    "Item 7. Management's Discussion and Analysis"
  ],
  "notes": "Fixture has div-based headers — tests header promotion heuristic."
}
```

Fields:
- `min_section_count` — minimum acceptable number of sections.
- `expected_headings` — list of headings that must appear in the output (substring match is acceptable).
- `notes` — description of what edge case this fixture targets.

---

## Step 3: Add a Test

Add a test in `tests/test_converter_benchmark.py` (for converter-focused tests) or `tests/test_pipeline.py` (for full pipeline tests):

```python
import pytest
from pathlib import Path
from zion_terminal.pipeline.filing_pipeline import FilingPipeline


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_my_new_fixture_sections():
    """My new fixture should extract >= 8 sections including Part I and Part II."""
    html = (FIXTURES_DIR / "my_new_fixture.html").read_text()
    pipeline = FilingPipeline()
    result = pipeline.process_html(html, form="10-K", ticker="TEST")

    assert result.success, f"Pipeline failed: {result.errors}"
    assert result.section_count >= 8, (
        f"Expected >= 8 sections, got {result.section_count}"
    )
    headings = [s["heading"] for s in result.sections]
    assert any("Part I" in h for h in headings), f"'Part I' not found in: {headings}"
    assert any("Part II" in h for h in headings), f"'Part II' not found in: {headings}"


@pytest.mark.slow
def test_my_new_fixture_performance():
    """Processing should complete in under 5 seconds."""
    import time
    html = (FIXTURES_DIR / "my_new_fixture.html").read_text()
    pipeline = FilingPipeline()
    t0 = time.time()
    result = pipeline.process_html(html, form="10-K", ticker="TEST")
    elapsed = time.time() - t0
    assert elapsed < 5.0, f"Processing took {elapsed:.2f}s — too slow"
```

---

## Step 4: Update docs/benchmarks/methodology.md

Add an entry to the Fixture Corpus section in `docs/benchmarks/methodology.md`:

```markdown
| `my_new_fixture.html` | 10-K | 48 KB | Nested tables, no explicit section headers — tests table-based segmentation fallback. |
```

---

## Step 5: Run the Full Benchmark Suite

After adding the fixture and its test, run the full suite to confirm nothing regresses:

```bash
# Run all benchmark tests
pytest tests/test_converter_benchmark.py tests/test_pipeline.py -v

# Include slow tests if your fixture is gated
pytest tests/ -v -m "not integration"

# Run the benchmark runner script for a full report
python scripts/run_benchmarks.py
```

Review the output of `scripts/run_benchmarks.py` and update `docs/benchmarks/latest.md` and `docs/benchmarks/latest.json` with the new fixture's results.

---

## Reference

- Existing fixtures: `tests/fixtures/`
- Benchmark methodology: `docs/benchmarks/methodology.md`
- Benchmark runner: `scripts/run_benchmarks.py`
- Regression policy: `docs/benchmarks/regression_policy.md`
- Pipeline module: `src/zion_terminal/pipeline/filing_pipeline.py`
