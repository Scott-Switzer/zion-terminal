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

Zion full Python 3.11 execution passed 483 tests, with 2 unrelated existing Arelle expectation failures after the declared environment installed Arelle. Zion Temporal Core and serving gates passed 25 tests, compilation, and diff checks. PPE's Python 3.11 environment was rebuilt from `pyproject.toml`; its complete run reached 4,482 passed and 51 skipped, with 40 pre-existing data/external-environment failures (missing local research tables, external browser/data dependencies, and scipy/playwright/xlrd requirements). PPE temporal/lakehouse/quarterly gates passed 17 tests and CI passed the required quality, data-plane, terminal-worker, container, and test checks.

The final thin Worker deployment is `f04d7bdc-46fc-4599-a0c6-328a58d5e025`. The custom domain returned HTTP 200 for health and its capability endpoint reported the exact schema and fingerprint. AAPL current revenue returned release `99d7088a2f0985e663a516a90c290f15`; no CURRENT change or release promotion occurred. Before/after availability queries, equivalent offset queries, naive/date-only rejection, unknown entity behavior, derived margin evidence, and the existing shadow comparator were verified. Shadow comparison reported 3 cases and 0 unexplained mismatches.

GitHub persistent Q1 runs against the custom domain, 100 requests each, were all HTTP 200 with one release identity:

| Run | p50 | p95 | p99 | max |
|---:|---:|---:|---:|---:|
| 1 | 166.63 ms | 233.92 ms | 372.89 ms | 2224.51 ms |
| 2 | 170.12 ms | 294.57 ms | 1164.21 ms | 2143.74 ms |
| 3 | 160.80 ms | 283.77 ms | 1306.58 ms | 2729.38 ms |

Q2/Q3/Q4/Q5 local custom-domain canaries returned HTTP 200 and the expected release; the previously established serving gates remain unchanged. Health reliability was 100/100 HTTP 200. No 1105, 503, or connection-reset incidents were observed in the GitHub run.

No historical R2 objects, Iceberg snapshots, serving release artifacts, production financial values, or CURRENT pointers were mutated.
