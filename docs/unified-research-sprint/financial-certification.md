# Metric and period certification

`financial_certification.certify_history(data, history_result, *, as_of,
reconciliation_evidence=None, accounting_inputs=())` labels each returned
observation and coverage gap. `VERSION` is `sec-financial-certification-v1`.
Input materializations must already be hash-verified and `PUBLIC_SEC_ONLY`;
licensed/private datasets are rejected before evidence processing. The function
never changes financial observations, fills a missing value, or chooses an older
source filing to make an identity balance.

## Evidence levels

| Status | What is established |
| --- | --- |
| SOURCE_OBSERVED | Exact native value, approved metric/concept/unit, issuer/member identity, source period, latest nonconflicting eligible source, cutoff and source-ID linkage |
| INDEPENDENTLY_RECONCILED | A separately identified implementation checked this exact value/metric/period/unit/frequency/source-ID set against this member and registry |
| DERIVATION_VERIFIED | Exact signed source terms reproduce the value and recognized FCF/debt or period-composition rules pass; this is not accounting certification |
| ACCOUNTING_CERTIFIED | An explicit metric-specific accounting identity reconciles with same-filing, form, availability, period and monetary scope, including required special evidence |
| UNAVAILABLE | Null, missing source, conflict, unsupported concept/unit/scope, industry mapping exclusion, invalid derivation, or explicit coverage gap |

`verified_statuses` preserves all applicable evidence levels; a derived or
accounting-certified value can also retain independent reconciliation. Every row
includes version, rule IDs, reasons and source evidence. Coverage gaps remain
unavailable with their original reason. An Assets/LiabilitiesAndStockholdersEquity
PASS never certifies revenue, income, cash flow, shares or debt.

Supported accounting identities are revenue minus cost equals gross profit;
gross profit minus operating expenses equals operating income; explicitly
scope-confirmed pretax income minus tax equals net income; beginning/end cash
movement including OCF, investing, financing and FX; diluted shares/EPS with
explicit common-income and rounding-precision evidence; and approved nonoverlapping
debt-component totals. Supplemental components must reference known normalized
source leaves. Net-income scope, rounding and debt-nonoverlap evidence must come
from a validated filing/definition review, not a convenient assumption.

The current production financial materialization normally lacks these additional
statement components. Its generic balance check cannot substitute for them.
Industry exclusions and unconfirmed debt components stay excluded under the
materialization's existing policy. The taxonomy-based statement family remains
a qualified classification, not a newly certified industry/SIC determination.

Reconciliation evidence is `{method, implementation_sha256, member_sha256,
registry_sha256, checks:[{status,metric,frequency,period_start,period_end,unit,
value_decimal,source_ids}]}`. Aggregate totals, an unrelated value or another
member's proof cannot upgrade a row. Accounting inputs are
`{identity_id,metric,period_start,period_end,component_ids:{role:source_id},...}`;
all referenced leaves must already be present in the supplied data copy.

## Bounded real public-SEC companions

`scripts/build_sec_metric_certification.py` reads at most 20 selected existing
public-SEC archive members. Existing exact local members are reused; remaining
reads use `CachedRangeArchive` and `materialize_sec_engine.extract`, pinned to the
stored ZIP ETag, with 8 MiB compressed/24 MiB raw member limits. Only SEC archive
paths are admitted. It neither acquires new data nor writes cloud objects.

An auxiliary whitelist normalizes CostOfRevenue, CostOfGoodsAndServicesSold,
GrossProfit and OperatingExpenses using the immutable core's exact source-ID
convention: SHA-256 of canonical `{cik,concept,unit,source_record}`. It retains
original decimal tokens. These auxiliary leaves are not new financial-history
metrics. They are pruned to explicit identity inputs and stored in small
companions, leaving original materializations and archives untouched.

At the September 24, 2026 archive cutoff, the 20-company build independently
checked 1,490 annual metric/period values against raw source terms in Python and
DuckDB DECIMAL arithmetic. The companion outputs totaled 2,546,344 bytes. Four
existing-archive range requests read 16,777,216 bytes; cloud writes were zero.

There were 158 accounting-certified historical rows across nine companies:
Apple, Microsoft, NVIDIA, Johnson & Johnson, Coca-Cola, PepsiCo, Home Depot,
Lowe's and Intel. All explicit same-filing identity comparisons passed. Another
1,209 rows had independently reconciled reported values; 123 had verified
derivations. All 1,490 rows retain independent evidence. These results do not
claim every metric or every company is accounting-certified.

The cohort's current discovery symbols overlap stored market histories, but that
is not a historical issuer/security join. No market rows were used for this
certification build. Fresh NIKE/Costco members were excluded so archive companions
cannot be silently applied to their newer member hashes.

Protected outputs live under
`/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-research-sprint/certification/`.
`companion-summary.json` records byte counts and content hashes. The exact executed
builder and certifier source bytes are retained in `implementation/`; each
reconciliation proof pins the executed builder hash. Subsequent repository-only
lint formatting does not replace that execution provenance.

API integration must verify companion `member_sha256` and `registry_sha256`,
append its `observations` to a copy of the matching core data, and pass its
`accounting_inputs` and `reconciliation_evidence` to `certify_history`. Unknown
members, fresh-source mismatches or unsupported evidence must fail closed.

## Existing quarantine metadata

The existing 20,396 checkpoints contain 15,136 usable and 5,260 quarantined
issuers. Classification used checkpoint status/reason/source-eligibility metadata
only, without a new archive inventory or financial-data reprocessing:

| Root cause | Issuers | Qualification |
| --- | ---: | --- |
| NO_US_GAAP_TAXONOMY | 3,778 | No US-GAAP taxonomy in this stored member; not a legal reporting/issuer-validity judgment |
| NO_US_PERIODIC_CONTEXTS | 1,324 | No supported periodic contexts in this source snapshot |
| SOURCE_IDENTITY_MISMATCH | 70 | Member CIK and payload CIK differ; no reassignment was guessed |
| ELIGIBLE_US_PERIODIC_SOURCE | 88 | Source metadata is eligible but no mapped usable history exists; further metric-specific investigation remains |

`quarantine-root-causes.json` records counts, limitations and a deterministic hash
of the inspected metadata. Quarantines do not block usable issuers or establish
that the remaining companies are fully certified.

The bounded retrospective expansion adds 21 actual 2019 filing points matched to stored owner price sessions. Separate protected `certification-2019-joined` companions bind 718 independently checked annual observations to their exact UTC-start financial cutoffs and actual B39 source member/registry versions. All 718 checks passed; nine companies have metric-specific accounting certificates. Existing proof execution snapshots remain retained, and the expanded historical builder is captured separately. Fresh NIKE uses its actual B39 member; no original-archive substitution is allowed. This local proof result precedes publication and live API validation.

The final unique union contains **31 companions and 2,388 current annual observations**: 1,977 independently reconciled observations, 232 derivation-verified observations, and 179 accounting-certified observations. Its 2,388 independent reconciliation checks agree with that observation count. The initial 20-company phase remains 1,490 observations and 158 accounting-certified observations; these phase counts must not be added to the union count. NIKE's companion cutoff is October 14, 2026 UTC start; other current companions retain September 24.

The earlier union metadata total of 3,370 was incorrect: the historical expansion producer counted unavailable coverage gaps in its status totals, while the initial producer counted observations only. Summing these different definitions added 982 gap statuses to the unique observation total. The union has 31 unique CIKs; overlap duplication was not the mechanism. Coverage gaps are reported separately (1,535 in the stored companion certification results). The protected aggregate correction receipt binds the exact companion hashes and counts; source values, proof checks, and published companion bytes are unchanged.

The final unambiguous 2019 cohort replaces ambiguous Johnson & Johnson/Procter & Gamble identity points with Honeywell/McDonald's. Its 21 exact points have 710 passing historical reconciliation checks. The earlier candidate cohort's 718 checks describe that earlier phase and are not the final joined total. The demonstration selects 20 companies and retains NIKE as the spare.
