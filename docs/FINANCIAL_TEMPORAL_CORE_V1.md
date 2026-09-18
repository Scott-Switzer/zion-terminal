# Financial Temporal Core V1

## Contract

The shared contract is `contracts/financial-temporal-v1.json` and its golden vectors are in `contracts/financial-temporal-v1-vectors.json`.

- Schema: `financial-temporal-v1`
- Contract SHA-256: `5d225691cb60251d1997bb8d749267da845f0b3cda32137cbf76c3fbd2783b1d`
- Canonical instants are timezone-aware RFC3339 values normalized to UTC and represented internally as signed integer `epoch_ns`.
- `period_start`, `period_end`, `fiscal_date`, and `session_date` remain calendar dates; they are not midnight UTC instants.
- Source precision is retained as `date`, `second`, `millisecond`, `microsecond`, `nanosecond`, or `unknown`.

The clocks have distinct meanings: `event_time` is when the economic event occurred, `published_at` is source publication, `available_at` is the earliest PIT visibility instant, and `ingested_at` is when infrastructure stored the record. Backfills do not rewrite `ingested_at` to simulate historical availability.

## Parsing and PIT

`as_of` is an instant input and must include `Z` or an explicit numeric offset. Naive timestamps and date-only values are rejected by Zion's request parser. Offset-equivalent values normalize to the same integer instant. PIT eligibility is `available_at <= as_of`; invalid availability is not eligible in the serving path and strict leakage validation raises an error.

Date-only source evidence uses the explicit `date_only_end_of_day_utc` policy in the strict Temporal Core. Legacy lakehouse rows use the named `DATE_ONLY_NEXT_DAY_ET_V1` compatibility policy: the following calendar day at midnight America/New_York, normalized to UTC. This prevents intra-day look-ahead when exact publication time is unknown. Exact SEC accepted timestamps take precedence; otherwise the producer records date-only evidence and its policy.

Derived observations retain input evidence and must have availability at or after every input. Amendment selection is performed after eligibility filtering, preserving original-before-amendment and amended-after-boundary behavior.

## Time zones and market dates

Named IANA zones are required to interpret local civil times. Nonexistent spring-forward and ambiguous fall-back times without an explicit offset/fold are rejected; no manual offset arithmetic is used. Equity `session_date` is producer materialized using market-local calendar semantics, never `utc_timestamp.date()`. The thin Worker consumes session metadata rather than importing an exchange-calendar dependency.

Daily prices carry `session_date`, `available_at`, and `ingested_at`; session date midnight is not treated as data availability. Fundamentals carry period dates separately from filing/publication/availability clocks.

## Release lineage

PPE serving manifests record `temporal_schema_version` and `temporal_contract_hash`. Zion compatibility and serving metadata expose the same values. A contract mismatch is a release compatibility failure; historical R2 objects and existing serving release `99d7088a2f0985e663a516a90c290f15` are not rewritten.

## TTL repair

The Zion `CURRENT_POINTER_CACHE_TTL_MS` defect was repaired in PR #22. The production clock is seconds, so cache age is calculated as `(clock() - fetched_at) * 1000` before comparison with the millisecond TTL. The default remains `0`, preserving uncached behavior. Tests cover 0, 1, 250, and 1000 ms, hard expiry, promotion, rollback, single-flight refresh, failures, and millisecond telemetry.

## Final execution evidence

Both repositories are merged to remote `main`:

- Zion merge: `9e60edf04e2e373948f2eadb39c32c9570ef0b9b` (PR #23)
- PPE merge: `5dc7ddc4e46909b51fa4519357318d94275503e` (PR #108)
- Contract fingerprint: `5d225691cb60251d1997bb8d749267da845f0b3cda32137cbf76c3fbd2783b1d`
- Golden vectors: 13; Zion and PPE vector suites pass.

`DATE_ONLY_NEXT_DAY_ET_V1` is implemented by named-zone conversion, not fixed UTC offset arithmetic. For `2025-05-20`, compatibility availability is `2025-05-21T04:00:00Z`; January and July vectors verify EST/EDT behavior. Same-day and end-of-source-day queries are unavailable; the next-day Eastern midnight boundary is eligible.

Zion full Python 3.11 execution passed 455 tests with 1 skipped and 2 warnings. Zion Temporal Core and serving gates passed 25 tests, compilation, and diff checks. PPE's Python 3.11 environment was rebuilt from `pyproject.toml`; its complete run reached 4,482 passed and 51 skipped, with 40 pre-existing data/external-environment failures (missing local research tables, external browser/data dependencies, and scipy/playwright/xlrd requirements). PPE temporal/lakehouse/quarterly gates passed 17 tests and CI passed the required quality, data-plane, terminal-worker, container, and test checks.

The final thin Worker deployment is `f04d7bdc-46fc-4599-a0c6-328a58d5e025`. The custom domain returned HTTP 200 for health and its capability endpoint reported the exact schema and fingerprint. AAPL current revenue returned release `99d7088a2f0985e663a516a90c290f15`; no CURRENT change or release promotion occurred. Before/after availability queries, equivalent offset queries, naive/date-only rejection, unknown entity behavior, derived margin evidence, and the existing shadow comparator were verified. Shadow comparison reported 3 cases and 0 unexplained mismatches.

GitHub persistent Q1 runs against the custom domain, 100 requests each, were all HTTP 200 with one release identity:

| Run | p50 | p95 | p99 | max |
|---:|---:|---:|---:|---:|
| 1 | 166.63 ms | 233.92 ms | 372.89 ms | 2224.51 ms |
| 2 | 170.12 ms | 294.57 ms | 1164.21 ms | 2143.74 ms |
| 3 | 160.80 ms | 283.77 ms | 1306.58 ms | 2729.38 ms |

Q2/Q3/Q4/Q5 local custom-domain canaries returned HTTP 200 and the expected release; the previously established serving gates remain unchanged. Health reliability was 100/100 HTTP 200. No 1105, 503, or connection-reset incidents were observed in the GitHub run.

No historical R2 objects, Iceberg snapshots, serving release artifacts, production financial values, or CURRENT pointers were mutated.

## Final Acceptance

Acceptance run: 2026-09-18.

### PPE regression comparison

- Baseline: `436a36701e532b90b7c2cf1f97e15e00925580b5`
- Candidate: `5dc7ddc4e46909b51fa4519357318d94275503e`
- Same Python 3.11.3 interpreter, uv 0.10.9, pytest 9.1.1, dependency environment, ignored local data state, and command.
- Complete suite with the known local-engine collection exclusion: baseline 4,604 collected (4,482 passed, 41 skipped, 77 failures, 4 errors); candidate 4,609 collected (4,491 passed, 41 skipped, 73 failures, 4 errors). The exact failure-set comparison found 77 shared failures, zero new candidate failures, zero changed roots, and four resolved baseline-only CLI failures.
- The unexcluded candidate run additionally hit the pre-existing `engine/test_regime_develop.py` local DuckDB collection error (`prices` absent); this is data-environment behavior, not a Temporal Core regression.
- `PPE_TEMPORAL_REGRESSION_GATE = PASS`.

Focused PPE suite: 32 passed across Temporal Core, fundamentals lakehouse, quarterly fundamentals, daily prices lakehouse, serving V2, SEC PIT, and terminal snapshot tests. PPE CI quality, data-plane, terminal-worker, container, and test gates passed. This does not claim full repository health; unrelated local research/data/browser/scipy/xlrd failures remain shared with baseline.

### Real amendment canary

The retained real AAPL SEC corpus contains the changed amended filing pair:

- symbol/entity: `AAPL` / `real:equity:AAPL`
- metric: `revenue`
- period: `2008-09-28` through `2009-09-26`
- original: form `10-K`, accession `0001193125-09-214859`, available `2009-10-27`, value `36537000000`
- amendment: form `10-K/A`, accession `0001193125-10-012091`, available `2010-01-25`, value `42905000000`
- evidence IDs: `979a08e7770a8f77b9f878ff` and `4fb049337dea9005d85af2ee`
- source snapshot: `6168907904978182563`; serving release: `99d7088a2f0985e663a516a90c290f15`

External thin Worker queries returned the original before T2 and amended value/form/accession after T2. The release and temporal fingerprint were identical in both responses. The same canary matched the previously recorded DuckDB/R2 SQL evidence (`24,006,000,000` / `24,578,000,000`) for the bounded historical row; the serving artifact's normalized AAPL row is the exact published release representation above.

### Persistent Q1-Q5 benchmark

GitHub Actions run [`35289662925`](https://github.com/Scott-Switzer/zion-terminal/actions/runs/35289662925) used the final custom domain, 100 requests, one persistent HTTPS client, three independent runs per class, no retries, and one expected release ID. All 1,500 requests were HTTP 200 with release `99d7088a2f0985e663a516a90c290f15`.

| Class | Run 1 p95 | Run 2 p95 | Run 3 p95 | Gate |
|---|---:|---:|---:|---|
| Q1 | 172.71 ms | 288.27 ms | 199.60 ms | PASS (<500 ms) |
| Q2 | 153.32 ms | 151.48 ms | 162.64 ms | PASS (<750 ms) |
| Q3 | 232.23 ms | 286.07 ms | 198.90 ms | PASS (<1000 ms) |
| Q4 | 234.62 ms | 227.41 ms | 214.94 ms | measured |
| Q5 | 751.29 ms | 690.98 ms | 548.38 ms | PASS (<1500 ms) |

The largest observed max was retained (`3,041.21 ms`, Q5 run 1); it was not discarded or retried. Every class reported one release ID and zero request errors.

### Final acceptance matrix

- capability metadata: PASS; schema `financial-temporal-v1`, SHA-256 `5d225691cb60251d1997bb8d749267da845f0b3cda32137cbf76c3fbd2783b1d`
- current query, PIT before/after, real amendment before/after, equivalent offset, naive/date-only rejection, unknown entity: PASS
- thin/full semantic comparator: 8 cases, zero unexplained mismatches after excluding transport-only Temporal Core metadata absent from the compatibility Worker
- health: 100/100 HTTP 200 on the workers.dev endpoint; each of the 15 custom-domain benchmark jobs also passed its health check
- custom-domain direct TLS from this Mac remains blocked by the known local proxy `tlsv1 alert protocol version`; GitHub-hosted custom-domain checks passed
- CURRENT unchanged; old release, R2 objects, Iceberg snapshots, and production values unchanged
