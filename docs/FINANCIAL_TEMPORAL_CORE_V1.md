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

## Validation status

Zion Temporal Core vectors and serving tests pass, as do PPE Temporal Core vectors. Existing lakehouse tests currently expose a compatibility issue in the legacy date-only amendment fixture: the fixture's date-only `available_at` is expected to be visible at the start of the filing date, while the new conservative end-of-day policy intentionally makes it visible only after that date. This must be resolved by migrating that fixture/source normalization to an explicit policy before declaring the milestone PASS; no production release or canonical object has been changed during this work.
