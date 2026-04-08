# ADR-0016: Benchmark Scoring and Regression Policy

**Status:** Accepted  
**Date:** 2026-04-08  
**Author:** Zion Terminal development team

---

## Context

The Zion Terminal benchmark suite evaluates the quality of the filing pipeline's output across multiple dimensions. We need:

1. A composite scoring formula that produces a single comparable number across runs.
2. A regression threshold that distinguishes noise from real degradation.
3. Clear procedures for handling detected regressions.

Without a defined policy, different engineers may interpret benchmark results differently, leading to inconsistent judgments about whether a change is acceptable.

---

## Decision

### Composite Score Formula

The benchmark composite score is a weighted average of three metrics:

```
composite = 0.35 × section_recovery + 0.45 × numeric_fidelity + 0.20 × table_fidelity
```

All three metrics are expressed as fractions in [0, 1].

| Metric | Weight | Rationale |
|---|---|---|
| Section recovery | 35% | Section structure is essential for usability — users navigate by section. Loss of sections is a serious degradation. |
| Numeric fidelity | 45% | Financial figures are the primary reason users fetch filings. Incorrect numbers are the most damaging failure mode. |
| Table fidelity | 20% | Tables convey multi-year comparisons. Important but partially redundant with numeric fidelity. |

**Parser accuracy** is tracked separately and is not included in the composite score. It is a property of the NL parser, not the filing pipeline.

### Regression Threshold

A regression is flagged when any tracked metric drops by more than **5%** relative to the previous baseline run:

```
regression = (previous - current) / previous > 0.05
```

| Severity | Condition | Required Action |
|---|---|---|
| Warning | 1–5% drop in any metric | Investigate before merging. Document if intentional. |
| Failure | > 5% drop in any metric | Block merge. Fix or explicitly accept and document. |
| OK | No drop, or improvement | No action required. |

The 5% threshold applies per-metric, not to the composite score alone. A drop in `section_recovery` is flagged even if the composite score is unchanged.

---

## Alternatives Considered

### Equal weights (33% / 33% / 33%)
Simpler but does not reflect the relative importance of numeric accuracy for a financial data terminal. Rejected.

### Single metric: section count
Too coarse. Does not capture numeric errors or table formatting quality. Rejected.

### LLM-graded output quality
Would produce more semantically meaningful scores but introduces non-determinism and cost. Rejected for the automated benchmark runner. Could be added as an optional manual review step.

### 10% regression threshold
Too lenient — a 10% drop in numeric fidelity means 1 in 10 numbers is wrong. Unacceptable for financial data. Rejected.

### 1% regression threshold
Too strict — natural variability in corpus sampling and fixture selection can cause 1–2% fluctuations without any code change. Would produce too many false alarms. Rejected.

---

## Tradeoffs

**Chosen policy (5% / weighted composite):**
- Pro: Clear, automatable, and reasonable for the current corpus size.
- Pro: Per-metric flagging ensures no single dimension can silently degrade.
- Con: Numeric fidelity (45%) requires XBRL ground truth, which requires Arelle. When Arelle is unavailable, the effective composite is `0.35 × section_recovery + 0.20 × table_fidelity` (renormalized), which may under-represent the most important metric.
- Con: The corpus is synthetic — the policy optimizes for synthetic benchmark performance, not real-world user experience.

---

## Consequences

1. `scripts/run_benchmarks.py` must compute and store the composite score in `latest.json`.
2. The runner must compare against `baseline.json` and exit non-zero if any metric drops > 5%.
3. CI (when configured) must fail on regression.
4. Intentional regressions require a documented entry in `docs/benchmarks/latest.md`.
5. When Arelle is unavailable, the composite is computed from available metrics only, and `xbrl_match_rate` is noted as `"unavailable"` in the report.

---

## Follow-up

- [ ] Implement composite score computation in `scripts/run_benchmarks.py`.
- [ ] Add CI step that fails on regression.
- [ ] Consider adding an LLM-graded quality dimension for manual review.
- [ ] Revisit weights after XBRL reconciliation is fully integrated.

---

## Reference

- Regression policy details: `docs/benchmarks/regression_policy.md`
- Benchmark system design: `docs/context/benchmarking_system_design.md`
- Related ADR: `docs/decisions/ADR-0017-xbrl-ground-truth-reconciliation.md`
