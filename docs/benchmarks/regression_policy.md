# Benchmark Regression Policy

This document defines how benchmark results are compared across runs and what constitutes a regression requiring action.

---

## Tracked Metrics

The following metrics are tracked in every benchmark run and compared against the previous run:

| Metric | Description | Source |
|---|---|---|
| `test_count` | Total number of passing tests | `pytest` output |
| `parser_accuracy` | Fraction of NL parser corpus queries correctly classified | `tests/fixtures/sample_queries.json` |
| `avg_section_recovery` | Average sections extracted per fixture | Fixture benchmark results |
| `fixtures_passing` | Number of fixtures that complete without errors | Fixture benchmark results |
| `xbrl_match_rate` | Fraction of XBRL facts matched in markdown | XBRL reconciliation (when Arelle available) |

Metrics are stored in `docs/benchmarks/latest.json` after each benchmark run.

---

## Default Thresholds

| Metric | Warning Threshold | Failure Threshold |
|---|---|---|
| `test_count` | Any decrease | Decrease > 5 tests |
| `parser_accuracy` | Drop of 1–5% | Drop > 5% |
| `avg_section_recovery` | Drop of 1–5% | Drop > 5% |
| `fixtures_passing` | Any decrease | Decrease > 1 fixture |
| `xbrl_match_rate` | Drop of 1–5% | Drop > 5% |

The **5% drop = warning** rule applies to all percentage-based metrics. An absolute drop of more than 5 percentage points in a single run is treated as a failure requiring investigation.

---

## How to Compare Runs

The benchmark runner script (`scripts/run_benchmarks.py`) writes results to `docs/benchmarks/latest.json`. To compare against a previous run:

```bash
# Save the current latest as the baseline before running
cp docs/benchmarks/latest.json docs/benchmarks/baseline.json

# Run benchmarks
python scripts/run_benchmarks.py

# Compare (example using jq)
jq '{
  parser_accuracy_delta: (.summary.parser_accuracy - $baseline.summary.parser_accuracy),
  test_count_delta: (.summary.total_tests - $baseline.summary.total_tests)
}' --argjson baseline "$(cat docs/benchmarks/baseline.json)" docs/benchmarks/latest.json
```

A future CI step should automate this comparison and post results as a PR comment.

---

## Handling Regressions

### Step 1: Identify the cause

Check the git log between the previous and current commits. Common causes:
- A new test was added that exposed an existing bug.
- A refactor changed segmentation or parsing behavior.
- A fixture was modified.
- A dependency was updated (e.g. `markdownify` version change).

```bash
git log --oneline <previous_commit>..HEAD
git diff <previous_commit> src/zion_terminal/pipeline/ src/zion_terminal/agents/
```

### Step 2: Classify the regression

| Type | Action |
|---|---|
| **Bug** | Fix the bug. Do not merge until the metric recovers. |
| **Intentional behavior change** | Document in changelog. Update baseline if the new behavior is correct. |
| **Fixture change** | If the fixture was updated, regenerate the expected values and document why. |
| **Flake / noise** | Re-run the benchmark. If the metric stabilizes, it may be measurement noise — consider increasing corpus size. |

### Step 3: Document intentional regressions

If a regression is intentional (e.g. a stricter heuristic produces fewer false sections), document it in the changelog and update `docs/benchmarks/latest.md`:

```markdown
## Intentional Metric Changes — v0.4.4

- `avg_section_recovery` dropped from 10.9 to 9.8: TOC discrimination tightened —
  previously included TOC entries as sections. New behavior is more correct.
```

### Step 4: Update the baseline

After resolving or documenting a regression, copy the new results as the baseline for the next comparison:

```bash
cp docs/benchmarks/latest.json docs/benchmarks/baseline.json
git add docs/benchmarks/
git commit -m "Update benchmark baseline after v0.4.4 regression resolution"
```

---

## Metrics NOT Tracked

The following are explicitly not tracked as regression metrics:

- **Runtime / latency** — fixture processing times vary by hardware. Use `@pytest.mark.slow` guards instead of hard latency assertions.
- **Cache hit rate** — depends on run order and environment.
- **Live API response times** — integration tests only; not included in the offline benchmark suite.

---

## Reference

- Benchmark runner: `scripts/run_benchmarks.py`
- Latest results: `docs/benchmarks/latest.json`
- Methodology: `docs/benchmarks/methodology.md`
- Known gaps: `docs/benchmarks/known_gaps.md`
