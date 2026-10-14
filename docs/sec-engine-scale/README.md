# Scaled private SEC research

500 archive issuers evaluated; 441 have usable mapped financial history. The 59 quarantines comprise three filename/JSON CIK mismatches and 56 issuers with no valid mapped history. Discovery covers 20,396 archived CIK names; this is not certification of 20,396 eligible U.S. listed companies. Stored census totals (125,508,338 observations /14,068 concepts) remain declared census figures, separate from independently verified outputs.

The generic engine verified 500,965 source revisions and 233,318 annual/quarterly/TTM observations. Thirteen rows with period ends after filing dates are rejected. Normalized source periods span 1967–2026; usable annual histories in this cohort span 2004–2026 and differ by company. Nine registered metrics include conditional FCF and debt; missingness and statement-family quarantines are explicit. Banks/insurers do not receive industrial revenue/FCF/debt mappings. Longer quarters require a contiguous annual calendar proof available by the cutoff. Overlapping source contexts invalidate dependent quarters/FCF/TTM. USD/shares remain native; no FX, missing-as-zero, inferred debt components or licensed data.

## Use the existing terminal

Credentials stay in `~/.config/zion/private-api.env` or the existing environment. Use the verified origin explicitly while custom TLS remains unresolved:

```sh
zion research companies --search Microsoft --base-url https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev
zion research history --cik 0000789019 --metrics revenue,net_income --date-from 2000-01-01 --all-pages --base-url https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev
zion research screen --metrics revenue_growth --minimum 0.1 --ciks 0000789019,0001018724,0001045810,0000104169 --limit 3 --base-url https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev
```

`python -m zion_terminal.private_research` runs the same command without initializing provider/orchestrator code. Credentials are never accepted as a command argument. JSON preserves exact decimal strings and ratio numerator/denominator. `scripts/sec_research_demo.py` actually runs the existing terminal, independently checks growth fractions, and retrieves three matched companies' histories (67 observations). See live-examples.json.

## Private API

All routes require the existing server-side bearer credential. GET `/private/v1/financial-history` retains CIK/metrics/frequency/date_from/date_to/as_of/expected_release_id/recipient and adds limit/cursor. Pagination returns total observations/gaps/checks and a signed, query/release-bound next_cursor. Follow every page for complete coverage; no silent truncation. Source records are complete for each page's observation/check dependencies. Authentication precedes source/cache reads.

GET `/private/v1/financial-companies?q=Microsoft`, `/financial-metrics?cik=0000789019`, and `/financial-coverage?cik=0000789019` provide discovery, mapping availability and coverage. Every path above uses the `/private/v1` prefix. Discovery's materialization state describes the immutable published catalog; newly queued computations are inspectable through coverage/history, not silently added to that catalog.

GET `/private/v1/financial-history/batch?ciks=0000789019,0001018724,0000019617&metrics=net_income,cash&date_from=2020-01-01` supports up to five companies with at most three concurrent tasks. GET `/private/v1/financial-screen?metric=revenue_growth&minimum=0.1&sort=desc` screens the compact immutable annual index. Thresholds and sorting use exact rational arithmetic. Its cutoff is fixed to the published source snapshot, 2026-09-24T00:00:00Z; other cutoffs are rejected. Each metric is the latest valid annual observation and can be stale or have a different fiscal end. Inspect returned periods; it is not a synchronized calendar-year comparison.

For unmaterialized source members, history returns202 with a durable job. POST `/private/v1/financial-jobs` accepts `{"ciks":["0000055785"],"metrics":["revenue","net_income"],"date_from":"2010-01-01"}`. GET the returned status URL, then its version-pinned history URL. Maximum20 companies/job, one company/message, consumer concurrency1, two retries, durable terminal failures. Unknown CIKs, source quarantines and unsupported forms/mappings remain explicit failures. Source/header/name/ETag/CRC/CIK checks precede immutable HMAC-authenticated computation caching.

Interactive bounds:25-second caller deadline,16MiB materialized source,12,000 normalized records,1,000 page items,2MiB response. Background source cap24MiB raw/8MiB compressed;120-second task deadline;30-second configured Worker CPU limit. Client all-pages cap100 pages/20MiB fails without partial output. These are measured scope boundaries, not certification of the full archive.

## Delivery evidence

230 Python tests, independent source/Python/DuckDB checks,125 live companies, full long-history comparisons, signed pagination/denials, cache-hit checks, private screener comparisons and the terminal workflow are recorded in acceptance-gates.json and protected artifacts. The actual Python bridge needs queue(batch,env,ctx); the one-argument documented example failed live and is covered by a reproducing test. Published native objects total about380MB; partitioned canonical revisions about30MB. Basin's single-company query still scanned207files, so no claim of efficient predicate pruning or narrowed runtime SQL privileges is made. No production CURRENT pointer changed.

Remaining work and exact operator actions are in operations.md. Incremental updates are implemented and one real candidate was published, but candidates are not automatically served or promoted. Prior releases and original archives remain intact. No full financial certification is claimed.
