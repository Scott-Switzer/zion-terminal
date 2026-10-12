# Dataset regression comparison (zion-financial-registry-comparison-v1)

- Baseline: `baseline` (full_report, as-of 2026-10-11)
- Candidate: `candidate` (full_report, as-of 2026-10-12)
- Overall severity: `WARNING`
- Finding counts: {"FAIL": 0, "INFO": 1, "PASS": 0, "WARNING": 14}
- Comparison digest: `73bbb07388892706af78f95a5d2b35b3720434957f34af1a24cc39cd47ed6188`

## Severity scale

| severity | meaning | CI exit status |
| --- | --- | --- |
| `PASS` | no material regression | 0 |
| `INFO` | expected new observations or documented source updates | 0 |
| `WARNING` | reduced coverage, staleness or an unresolved change | 1 |
| `FAIL` | broken integrity, contradictory identity, prohibited accounting substitution or a material unexplained regression | 2 |

## Companies

| ticker | CIK baseline | CIK candidate | severity | baseline overall | candidate overall |
| --- | --- | --- | --- | --- | --- |
| AAPL | 0000320193 | 0000320193 | PASS | SOURCE_PARTIALLY_VALIDATED | SOURCE_PARTIALLY_VALIDATED |
| MSFT | 0000789019 | 0000789019 | PASS | SOURCE_PARTIALLY_VALIDATED | SOURCE_PARTIALLY_VALIDATED |
| NVDA | 0001045810 | 0001045810 | WARNING | SOURCE_PARTIALLY_VALIDATED | SOURCE_PARTIALLY_VALIDATED |

### Report level

| severity | code | field | period | note |
| --- | --- | --- | --- | --- |
| INFO | `REPORT_CONTENT_ADDRESS_CHANGED` | — | — | the report content address changed; severity is decided by the classified findings below, never by the address alone |

### NVDA

| severity | code | field | period | note |
| --- | --- | --- | --- | --- |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | depreciation_amortization | 2024-01-28 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | diluted_weighted_average_shares | 2024-01-28 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | operating_income_ebit | 2024-01-28 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | revenue | 2024-01-28 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | depreciation_amortization | 2025-01-26 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | diluted_weighted_average_shares | 2025-01-26 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | operating_income_ebit | 2025-01-26 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | revenue | 2025-01-26 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | cash_and_equivalents | 2026-01-25 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | debt_noncurrent | 2026-01-25 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | depreciation_amortization | 2026-01-25 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | diluted_weighted_average_shares | 2026-01-25 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | operating_income_ebit | 2026-01-25 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |
| WARNING | `NON_INDEPENDENT_CORROBORATION_PREEXISTING` | revenue | 2026-01-25 | an existing validation state rests on evidence that is not independent: every recorded corroborating source references the same stored object this registry read; agreement on one source is not independent corroboration |

## Not evaluated

- MSFT depreciation_amortization 2024-06-30: MISSING_OBSERVATION unchanged from the baseline
- MSFT depreciation_amortization 2025-06-30: MISSING_OBSERVATION unchanged from the baseline
- MSFT debt_short_term_borrowings 2026-06-30: MISSING_OBSERVATION unchanged from the baseline
- MSFT debt_short_term_total 2026-06-30: MISSING_OBSERVATION unchanged from the baseline
- MSFT depreciation_amortization 2026-06-30: MISSING_OBSERVATION unchanged from the baseline
- NVDA: blocking issue PUBLISHED_SEC_FACTS_NOT_IN_RELEASE persists unchanged
- NVDA debt_short_term_borrowings 2026-01-25: MISSING_OBSERVATION unchanged from the baseline
- NVDA debt_short_term_total 2026-01-25: MISSING_OBSERVATION unchanged from the baseline

## Interpretation rules

- A changed report content address is never a regression by itself.
- Agreement between two adapters that read the same underlying SEC source is not independent corroboration and never promotes a state by itself.
- A value change is explained only by a new accession/filing or a changed source object revision; anything else is unexplained.
- Issuer CIK, concept, unit and fiscal period identify an observation; a change in any of them is a different observation, not a restated one.
