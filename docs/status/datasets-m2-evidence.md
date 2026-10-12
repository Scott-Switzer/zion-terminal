# Dataset regression canary M2 evidence

Milestone 2A of the private financial dataset registry: a reproducible
data-quality regression canary over the three-company validation.

- Baseline (approved): `artifacts/registry-m2/approved-baseline.json`, as-of
  `2026-10-11`, content address `a107fecd673573038f46544aceebdbf9b9f126a05b4dd66a11b099f59dcaf15e`
  — identical to the M1 digest, so the live evaluation is reproducible across
  runs and across milestones.
- Candidate: a fresh live report as-of `2026-10-12`, content address
  `99dd945542e1ef0b46e4f9093d0c6c39277014a6e3e17ad34ed3727091659ccb`.
- Comparison: `73bbb07388892706af78f95a5d2b35b3720434957f34af1a24cc39cd47ed6188`,
  overall severity **`WARNING`** (14 WARNING, 1 INFO, 0 FAIL), exit status 1.

The sanitized comparison is committed next to this file
([datasets-m2-comparison.json](datasets-m2-comparison.json),
[datasets-m2-comparison.md](datasets-m2-comparison.md)); the private comparison
with object identity stays in the git-ignored `artifacts/registry-m2/`. The
sanitized baseline is committed as
[datasets-m2-baseline.summary.json](datasets-m2-baseline.summary.json) for
comparisons from a fresh clone.

## Commands that work

```bash
# offline, no credentials: the scheduled canary path
python tests/fixtures/datasets/build_comparison_fixtures.py
git diff --exit-code tests/fixtures/datasets/comparisons      # corpus is reproducible
PYTHONPATH=src python -m zion_terminal.datasets report --tickers TSTA,TSTB \
    --as-of 2026-10-11 --out artifacts/canary --fixture tests/fixtures/datasets/tree
PYTHONPATH=src python -m zion_terminal.datasets compare \
    --baseline tests/fixtures/datasets/baselines/fixture-report.json \
    --candidate artifacts/canary/report.json --out artifacts/canary/compare \
    --expect-severity PASS                                    # exit 0

# live, operator bridge required: bootstrap once, then compare
PYTHONPATH=src python scripts/dataset_canary.py --as-of 2026-10-11 \
    --accept-baseline --reason "adopt the M1 approved three-company baseline" --actor <operator>
PYTHONPATH=src python scripts/dataset_canary.py --as-of 2026-10-12     # exit 1 (WARNING)

# any scenario through the CLI
PYTHONPATH=src python -m zion_terminal.datasets compare \
    --baseline tests/fixtures/datasets/comparisons/unexplained_value_change/baseline.json \
    --candidate tests/fixtures/datasets/comparisons/unexplained_value_change/candidate.json \
    --out /tmp/cmp                                                # exit 2 (FAIL)
```

## Severity examples from the tested fixtures

23 committed scenario pairs; expected severities are hand-declared in
`tests/unit/test_dataset_compare.py`.

| scenario | fixtures show | overall | exit |
|---|---|---|---|
| `unchanged` | identical reports, identical content address | `PASS` | 0 |
| `leakage_attempt` | identical evaluated content, moved address only | `INFO` | 0 |
| `restatement` | later accession for the same period, 1.5% value move | `INFO` | 0 |
| `new_period` | a newly published annual period | `INFO` | 0 |
| `field_lost` | a previously available field disappears | `WARNING` | 1 |
| `observation_lost` | usable observation becomes `MISSING_OBSERVATION` | `WARNING` | 1 |
| `validation_downgrade` | `SOURCE_VALIDATED` → `SOURCE_UNVALIDATED` | `WARNING` | 1 |
| `source_version_mismatch` | same accession, changed source object, 0.2% move | `WARNING` | 1 |
| `debt_concept_demoted` | `LongTermDebtCurrent` → `DebtCurrent` | `WARNING` | 1 |
| `unexplained_value_change` | material change, same accession, same object | `FAIL` | 2 |
| `invalid_unit_conversion` | 1000× move with an unchanged unit | `FAIL` | 2 |
| `contract_unit_violation` | `USD` → `shares` for a balance | `FAIL` | 2 |
| `prohibited_substitution` | `NetIncomeLoss` claimed as common income | `FAIL` | 2 |
| `forward_availability` | usable state available after the as-of instant | `FAIL` | 2 |
| `issuer_identity_change` | one ticker, a different CIK | `FAIL` | 2 |
| `ticker_collision` | two CIKs resolving to one ticker | `FAIL` | 2 |
| `non_independent_validation` | validation asserted on the same stored object | `FAIL` | 2 |
| `manifest_integrity_failure` | release stops resolving, manifest sample vanishes | `FAIL` | 2 |
| `blocked_company` | source facts lost, new blocker | `FAIL` | 2 |
| `sanitized_pair` | summaries without provenance: provenance checks skipped | `WARNING` | 1 |
| `incomparable_pair` / `contract_mismatch` | refused (`INCOMPARABLE_REPORTS`) unless overridden | refused | 2 |

## AAPL / MSFT / NVDA live comparison

Baseline as-of `2026-10-11` against candidate as-of `2026-10-12`, both live
through the loopback `zion-datasets` bridge:

| issuer | CIK | overall baseline | overall candidate | comparison severity | findings |
|---|---|---|---|---|---|
| AAPL | 0000320193 | `SOURCE_PARTIALLY_VALIDATED` | `SOURCE_PARTIALLY_VALIDATED` | `PASS` | none |
| MSFT | 0000789019 | `SOURCE_PARTIALLY_VALIDATED` | `SOURCE_PARTIALLY_VALIDATED` | `PASS` | none |
| NVDA | 0001045810 | `SOURCE_PARTIALLY_VALIDATED` | `SOURCE_PARTIALLY_VALIDATED` | `WARNING` | 14 × `NON_INDEPENDENT_CORROBORATION_PREEXISTING` |

Nothing regressed: no field was lost, no value moved, no state changed, no
identity changed and no provenance check degraded between the two days. The one
report-level `INFO` is `REPORT_CONTENT_ADDRESS_CHANGED`, the documented
consequence of the as-of instant being part of the report body — explicitly not
a severity signal.

The 14 NVDA warnings are a real finding of this milestone, and they are reported
as a *pre-existing condition*, not as a new regression. NVDA's corroboration
evidence consists of an approved valuation packet whose recorded SEC source is
the same archive member this registry reads
(`approved_packet_sec_source_cash` = `MATCH`), so 14 fields labelled
`SOURCE_VALIDATED` rest on one underlying source object rather than on
independent evidence. AAPL and MSFT carry a `DIFFERENT_SOURCE_OBJECT` check, so
their validation does rest on a distinct object and reports `PASS`. The
comparator refuses to treat the shared-source agreement as validation: a
*newly* asserted state on that basis is `FAIL`, and the existing condition is
`WARNING` — never a silent pass.

## Tests

| check | command | result |
|---|---|---|
| dataset unit tests (transport, registry, contract, report, CLI, compare) | `pytest tests/unit/test_dataset_*.py tests/unit/test_sec_fundamentals_contract.py -q` | **118 passed** |
| comparison library (`test_dataset_compare.py`) | `pytest tests/unit/test_dataset_compare.py -q` | **39 passed** |
| comparison CLI (`test_dataset_compare_cli.py`) | `pytest tests/unit/test_dataset_compare_cli.py -q` | **11 passed** |
| full Zion suite | `pytest tests -q` | **615 passed, 1 skipped** |
| lint | `ruff check src/zion_terminal/datasets scripts/dataset_canary.py tests/unit/test_dataset_compare*.py tests/fixtures/datasets/build_comparison_fixtures.py` | clean |
| format | `ruff format --check` over the same paths | clean |

Before this milestone the dataset suite was 68 tests and the full suite was 565
passed / 1 skipped.

## CI

| workflow | run | head | status |
|---|---|---|---|
| Validate private dataset service | [38286445740](https://github.com/Scott-Switzer/zion-terminal/actions/runs/38286445740) | `1b4411d1` (code) | success |
| Dataset regression canary (offline) | [38286444630](https://github.com/Scott-Switzer/zion-terminal/actions/runs/38286444630) | `1b4411d1` (code) | success |
| Dataset regression canary (offline), push event | [38286436657](https://github.com/Scott-Switzer/zion-terminal/actions/runs/38286436657) | `1b4411d1` (code) | success |

Both runs pass at the head that carries the code; the evidence-only commits that
follow are documentation and re-run both workflows on their own heads.

The canary workflow job performs four checks: the committed comparison corpus
must match its generator byte-for-byte, every documented scenario must keep its
declared severity, a freshly built synthetic report must compare `PASS` against
the approved fixture baseline, and the new files must stay lint- and
format-clean.

## Canary status, stated plainly

- **Offline canary: active.** `.github/workflows/datasets-regression-canary.yml`
  runs on the schedule `17 6 * * *` and on relevant pushes/PRs. It touches no
  private data and needs no credentials.
- **Live canary: operator-run, not unattended.** `scripts/dataset_canary.py`
  needs the authenticated loopback bridge to the private `zion-datasets` worker.
  Hosted runners do not have it, and publishing that worker to obtain it is out
  of bounds. Unattended live screening therefore requires either a self-hosted
  runner with bridge access pinned to that runner group, or a Cron Trigger inside
  the private worker that posts the sanitized comparison summary to the operator
  channel. Until one of those exists, no unattended live monitoring is claimed.
- **Baseline advancement is explicit and auditable.** The bootstrap run above
  recorded `artifacts/registry-m2/baseline_history.jsonl` with the actor, the
  reason, the mode, the digests and the timestamp. A `FAIL` comparison refuses to
  advance the baseline, and a `WARNING` needs `--allow-warning-baseline`.

## Remaining blockers / limitations

1. **NVDA corroboration is single-source** (14 fields). Fixing it is serving-side
   work: publish an independent D&A, capex and debt-split metric so the second
   source is a distinct object rather than a second adapter over the same object.
2. **The report ceiling is unchanged from M1**: the serving layer publishes no
   D&A, capex or debt-split metrics, so those fields stay `SOURCE_UNVALIDATED`.
   This is the honest state, not a defect of the comparator.
3. **Fixture mode proves the pipeline offline**, not live R2 bytes; the live
   comparison requires the operator bridge.
4. **`PUBLISHED_SEC_FACTS_NOT_IN_RELEASE` persists for NVDA** as an unchanged
   blocking issue, recorded in `skipped_checks` rather than re-reported.
5. **Unrelated CI failures are unchanged and were not touched**: `Tool Contract
   V2 Operations` (`38249215000`) and `Serving V2 Custom Domain Smoke`
   (`38240047945`) still fail on the staging serving-release mismatch
   (`release mismatch: 0a318ed6667fcf4c4d80e8d1c44def7a`) at `4055a821`. They are
   tracked separately in issue [#47](https://github.com/Scott-Switzer/zion-terminal/issues/47);
   no R2 pointer or expected release value was changed to silence them.
