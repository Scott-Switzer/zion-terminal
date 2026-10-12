# Dataset regression canary (M2)

The M1 registry answers *which contract fields are present, in the right units,
from an eligible filing, and corroborated by a second source*. This milestone
answers the follow-up question: **did anything about that answer change, and does
the change matter?**

`src/zion_terminal/datasets/compare.py` compares two report generations and
classifies every difference into a machine-readable finding with an explicit
severity. `python -m zion_terminal.datasets compare` exposes it with a CI exit
status, `tests/fixtures/datasets/comparisons/` is the synthetic scenario corpus,
and `scripts/dataset_canary.py` is the operator-run live wrapper.

Nothing here writes, copies, re-points or publishes a dataset, and nothing here
is wired to the public valuation site.

## Two halves, honestly labelled

| half | where it runs | private access | status |
|---|---|---|---|
| offline canary | GitHub Actions, schedule `17 6 * * *` (`.github/workflows/datasets-regression-canary.yml`) | none: synthetic fixture tree + committed baseline | **active** |
| live canary | operator machine with the loopback bridge to the private `zion-datasets` worker (`scripts/dataset_canary.py`) | the authenticated bridge | **operator-run, not unattended** |

Unattended *live* screening is deliberately not enabled. GitHub-hosted runners
have no access to the private worker, and publishing that worker to make
scheduling easier is exactly what the storage boundary forbids. The exact
prerequisite for enabling it is recorded in
[Prerequisite for unattended live monitoring](#prerequisite-for-unattended-live-monitoring).

## What is compared, and by which key

Two observations are the same observation only when they share the stable key:

- issuer CIK,
- contract field,
- fiscal period end,
- unit,
- reported concept and its accounting basis,
- and, where the input carries it, the source object revision, the accession and
  the availability cutoff.

A change in any key element is a **different observation**, not a restated one;
it is classified as such (`CONCEPT_PRIORITY_DEMOTED`,
`PROHIBITED_BASIS_SUBSTITUTION`, `CONTRACT_UNIT_VIOLATION`, `UNIT_CHANGED`).
A change in the value is explained only by a new accession/filing
(`RESTATEMENT_WITH_NEW_FILING`) or by a changed stored source object
(`VALUE_CHANGED_WITH_SOURCE_REVISION`). Everything else is
`VALUE_CHANGED_WITHOUT_SOURCE_REVISION`: a material unexplained regression.

A report's **content address is never a regression signal on its own**. The
digest covers `generated_at`-free content, so it moves whenever anything moves;
a moved address is `REPORT_CONTENT_ADDRESS_CHANGED` (`INFO`) and the severity is
decided only by the classified findings.

## Severity

| severity | meaning | CI exit status |
|---|---|---|
| `PASS` | no material regression | 0 |
| `INFO` | expected new observations or documented source updates | 0 |
| `WARNING` | reduced coverage, staleness or an unresolved change | 1 |
| `FAIL` | broken integrity, contradictory identity, prohibited accounting substitution or a material unexplained regression | 2 |

### Finding codes

| code | severity | trigger |
|---|---|---|
| `REPORT_CONTENT_ADDRESS_CHANGED` | INFO | the digest moved; never decisive alone |
| `PERIOD_OBSERVED_ADDED` / `NEW_PERIOD_FIELD_OBSERVED` | INFO | a newly published fiscal period |
| `RESTATEMENT_WITH_NEW_FILING` | INFO | value changed under a later accession |
| `STATE_IMPROVED`, `VALIDATION_UPGRADE`, `COMPANY_OVERALL_UPGRADE` | INFO | a real, evidenced improvement |
| `FIELD_GAINED`, `COMPANY_ADDED`, `BLOCKING_ISSUE_RESOLVED`, `SOURCE_FAMILY_RECOVERED` | INFO | coverage recovered |
| `CONCEPT_CHANGED`, `UNIT_CORRECTED`, `SOURCE_IDENTITY_DIVERGENCE_OBSERVED` | INFO | a documented, non-basis change |
| `IMMATERIAL_VALUE_DRIFT` | INFO | change inside both documented bounds |
| `FIELD_LOST` | WARNING | a previously available field disappeared |
| `OBSERVATION_LOST` | WARNING | a usable observation became `MISSING_OBSERVATION` |
| `VALIDATION_DOWNGRADE` | WARNING | validated became unvalidated |
| `POINT_IN_TIME_ELIGIBILITY_LOST`, `PERIOD_ALIGNMENT_LOST`, `OBSERVATION_NO_LONGER_CONFORMS` | WARNING | eligibility/alignment/definition lost |
| `PERIOD_OBSERVED_LOST`, `COMPANY_DROPPED`, `AS_OF_REGRESSION` | WARNING | coverage shrank |
| `CONCEPT_PRIORITY_DEMOTED`, `UNIT_CHANGED` | WARNING | a lower-quality but declared measure replaced a better one |
| `VALUE_CHANGED_WITH_SOURCE_REVISION` | WARNING | same accession, changed source object |
| `SOURCE_IDENTITY_REGRESSION` | WARNING | the second source no longer points at the object read here |
| `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | WARNING | an existing validation rests on one source object |
| `VALUE_CHANGED_WITHOUT_SOURCE_REVISION` | FAIL | material change, no new filing, no source revision |
| `SUSPECTED_UNCONVERTED_SCALE` | FAIL | a 10^3/10^6/10^9 move with an unchanged unit |
| `CONTRACT_UNIT_VIOLATION` | FAIL | unit outside the contract unit for the field |
| `PROHIBITED_BASIS_SUBSTITUTION` | FAIL | a basis-changing concept (for example consolidated `NetIncomeLoss` for common income) |
| `BASIS_SUBSTITUTION_UNSUPPORTED` | FAIL | a non-contract tag reported as a usable observation |
| `SIGN_CONVENTION_VIOLATION` | FAIL | sign flipped on a magnitude field |
| `FUTURE_AVAILABILITY_UNSUPPORTED` | FAIL | a usable state whose availability instant is after the report's own as-of |
| `AVAILABILITY_CUTOFF_INCONSISTENT`, `FILING_DATE_INCONSISTENT` | FAIL | filing/availability evidence moved inconsistently for one accession |
| `FILING_REGRESSION_EARLIER_ACCESSION` | FAIL | the candidate observation comes from an earlier filing |
| `ISSUER_IDENTITY_CHANGED`, `ISSUER_IDENTITY_UNRESOLVED`, `TICKER_IDENTITY_COLLISION` | FAIL | contradictory issuer identity |
| `SOURCE_FAMILY_UNRESOLVED`, `MANIFEST_ARTIFACT_SAMPLE_INCOMPLETE` | FAIL | broken integrity: a release stopped resolving, or sampled manifest artifacts vanished |
| `BLOCKING_ISSUE_ADDED`, `COMPANY_OVERALL_DOWNGRADE` | FAIL | a new blocker or a company roll-up collapse |
| `NON_INDEPENDENT_CORROBORATION` | FAIL | a state *newly* asserted on one stored source object |
| `MODE_MISMATCH`, `CONTRACT_SCHEMA_MISMATCH` | FAIL | inputs that are not comparable, only reachable with an explicit override |
| `STATE_CHANGED` | INFO | same-rank state move |

### Thresholds

Explicit, testable and printed into every comparison result:

- `value_drift_relative = 0.0005` (0.05% of the baseline value)
- `value_drift_absolute = 1000000` (USD 1,000,000 in the field unit)
- a change is immaterial **only** when it is inside both bounds;
- `scale_shift_factors = 1000, 1000000, 1000000000` with a 0.01% tolerance
- `future_availability_grace = 0`; the point-in-time comparison uses the same
  date-only, string-ordered convention as `sec_fundamentals.evaluate_company`

### Independent corroboration

Agreement between two adapters that read the *same* underlying SEC source is not
independent corroboration, and it never promotes a state by itself. The
comparator reads the report's own source-identity checks: a corroboration counts
as independent only when the report records a corroborating source object
**distinct** from the object read here. A newly claimed validation resting on one
object is `NON_INDEPENDENT_CORROBORATION` (`FAIL`); the same condition carried
over unchanged from the baseline is
`NON_INDEPENDENT_CORROBORATION_PREEXISTING` (`WARNING`, never a silent pass).
When the inputs carry no provenance (sanitized summaries) the check is recorded
in `skipped_checks` instead of being assumed.

## CLI

```bash
# compare two reports (private report.json or sanitized summary.json)
PYTHONPATH=src python -m zion_terminal.datasets compare \
    --baseline artifacts/registry-m2/approved-baseline.json \
    --candidate artifacts/registry-m2/candidate/report.json \
    --out artifacts/registry-m2/comparison

# CI gate: exit 0 only when the observed severity matches exactly
PYTHONPATH=src python -m zion_terminal.datasets compare \
    --baseline tests/fixtures/datasets/baselines/fixture-report.json \
    --candidate artifacts/canary/report.json \
    --out artifacts/canary/compare --expect-severity PASS

# sanitized-only run (no private comparison artifact is written)
PYTHONPATH=src python -m zion_terminal.datasets compare \
    --baseline BASE.json --candidate NEW.json --out OUT --summary-only
```

Exit status without `--expect-severity` follows the severity scale; with
`--expect-severity` it is `0` only on an exact match and `2` otherwise, so any
change in the classified findings fails the run in either direction. `compare`
needs no bridge and never contacts the private service; incomparable inputs
(mode or contract mismatch) are refused with `INCOMPARABLE_REPORTS` unless the
matching `--allow-*` override is passed.

Outputs, split by sensitivity exactly like the M1 report:

| artifact | contents | destination |
|---|---|---|
| `comparison.json` | findings with per-field private object identity | ignored (`artifacts/registry-m2/`) |
| `comparison.md` | the same, human-readable | ignored |
| `summary.json` | findings without paths, revisions or hashes | safe to commit |
| `summary.md` | the same as markdown | safe to commit |

`assert_no_private_metadata` runs over every sanitized artifact before it is
written and raises `LeakageError` on a store path, a 32+ hex object hash, a
bucket name, a member name, a credential-shaped string or an `etag`/`sha256`
field. The comparison's own content address is the single allowed digest.

## Scenario corpus

`tests/fixtures/datasets/comparisons/` holds 23 committed baseline/candidate
pairs, regenerated deterministically by
`tests/fixtures/datasets/build_comparison_fixtures.py` (the canary workflow
re-runs the generator and fails on any drift). Expectations are hand-declared in
`tests/unit/test_dataset_compare.py`, not derived from the implementation.

| scenario | overall | findings |
|---|---|---|
| `unchanged` | `PASS` | {} |
| `leakage_attempt` | `INFO` | {"INFO": 1} |
| `new_period` | `INFO` | {"INFO": 17} |
| `restatement` | `INFO` | {"INFO": 2} |
| `field_lost` | `WARNING` | {"INFO": 1, "WARNING": 1} |
| `observation_lost` | `WARNING` | {"INFO": 1, "WARNING": 1} |
| `debt_concept_demoted` | `WARNING` | {"INFO": 1, "WARNING": 2} |
| `validation_downgrade` | `WARNING` | {"INFO": 1, "WARNING": 1} |
| `source_version_mismatch` | `WARNING` | {"INFO": 1, "WARNING": 1} |
| `as_of_regression` | `WARNING` | {"INFO": 1, "WARNING": 1} |
| `sanitized_pair` | `WARNING` | {"INFO": 1, "WARNING": 1} |
| `unexplained_value_change` | `FAIL` | {"FAIL": 1, "INFO": 1} |
| `invalid_unit_conversion` | `FAIL` | {"FAIL": 1, "INFO": 1} |
| `contract_unit_violation` | `FAIL` | {"FAIL": 1, "INFO": 1} |
| `prohibited_substitution` | `FAIL` | {"FAIL": 1, "INFO": 1} |
| `sign_convention` (library test) | `FAIL` | {"FAIL": 1, "INFO": 1} |
| `forward_availability` | `FAIL` | {"FAIL": 1, "INFO": 1} |
| `issuer_identity_change` | `FAIL` | {"FAIL": 1, "INFO": 1} |
| `ticker_collision` | `FAIL` | {"FAIL": 1, "INFO": 1} |
| `manifest_integrity_failure` | `FAIL` | {"FAIL": 2, "INFO": 1} |
| `blocked_company` | `FAIL` | {"FAIL": 2, "INFO": 1, "WARNING": 1} |
| `non_independent_validation` | `FAIL` | {"FAIL": 6, "INFO": 8} |
| `incomparable_pair` | refused | `mode mismatch` without the override; `MODE_MISMATCH` with it |
| `contract_mismatch` | refused | `contract schema mismatch` without the override |

The `INFO` in most rows is `REPORT_CONTENT_ADDRESS_CHANGED`, the documented
consequence of a mutated candidate.

## Baseline policy

The approved baseline is `artifacts/registry-m2/approved-baseline.json`
(git-ignored, it carries private provenance). The committed sanitized baseline
`docs/status/datasets-m2-baseline.summary.json` is the degraded fallback used
when no private baseline exists; it makes the public fields comparable from a
fresh clone but cannot evaluate provenance checks, which is recorded in
`skipped_checks`.

Advancement rules, enforced by `scripts/dataset_canary.py`:

1. advancement is never automatic: it requires `--accept-baseline`;
2. it requires a `--reason` (and records `--actor`) so the change is auditable;
3. a `FAIL` run **never** advances the baseline and exits 2;
4. a `WARNING` run needs `--allow-warning-baseline` and exits 1 otherwise;
5. every accepted baseline is appended to `baseline_history.jsonl` with the
   previous digest, the new digest, the comparison digest, the actor, the reason
   and the timestamp.

## Prerequisite for unattended live monitoring

The live half runs where the private worker is reachable. To make it unattended
without publishing the worker, one of these is required, with its own gate:

1. a self-hosted runner on the operator network that can reach the loopback
   bridge and holds the bridge credentials as runner secrets, with the schedule
   pinned to that runner group; or
2. a Cloudflare Cron Trigger *inside* the existing private `zion-datasets`
   worker that builds the candidate report and posts the comparison summary to
   the operator's alert channel, so no private bytes ever leave the account.

Until one of those exists, scheduled coverage is the offline half only, and the
live comparison stays operator-run. That is a deliberate limitation, not an
oversight: publishing `zion-datasets` or widening its bindings to make a hosted
runner work would break the storage boundary the registry is built on.
