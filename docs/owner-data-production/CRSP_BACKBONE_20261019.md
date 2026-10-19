# CRSP-first production checkpoint — 2026-10-19

User direction: CRSP and other stored WRDS datasets are the historical backbone; existing Yahoo Finance and other provider observations supplement that archive. Alpaca collects ongoing data. Estimated WRDS access cutoff supplied by user: May 2027, with summer access uncertain. User requires Codex task work to stop by 25% remaining account usage; do not resume broad work without checking that constraint.

## Deployed functionality

Production workspace: https://dcf-valuation-engine.scswitzer.workers.dev/data . Production API aliases remain `/api/data/stock-history` and `/private/v1/stock-history`; the installed Zion credential file points at this production origin.

- Default source: `crsp`. Catalog has 40,518 native PERMNO security IDs; 110,245,749 stored daily rows across 221 original files, indexed into 866,640 date-verified calendar-year spans. Source dates span 1925-12-31 through 2025-12-31. Names/tickers retain native dated intervals; unknown names remain explicit.
- `alpaca_latest`: the existing hash-verified daily publication, dynamically read from `control/daily-prices/CURRENT.json`. The 2026-10-19 release has 11,497 valid SIP bars from 12,134 registered targets, with 637 explicit no-session-bar gaps and zero provider failures, invalid rows or duplicate keys.
- Stored provider sources: `yfinance`, `simfin`, `tiingo`, `eodhd`, `marketstack`, `polygon`, `twelve_data`, `estate_alpaca`. All 3,485,616 existing observations from 86 source files were indexed into 2,953 provider-qualified source IDs. These IDs overlap across providers and are not 2,953 additional distinct stocks. The `alpaca` source separately preserves the earlier 2,629-symbol raw archive.
- CLI uses the same API: `zion stocks --permno 14593 --date-from 2025-01-01 --limit 5`; `zion stocks --source alpaca_latest --symbol AAPL`; `zion stocks --source yfinance --search AAPL`, then use returned source ID with `--security-id`.

Stored-provider row counts: Yahoo 159,172; SimFin 2,746,424; Tiingo 326,619; EODHD 45,554; Marketstack 8,817; Polygon 16,047; Twelve Data 151,344; normalized estate Alpaca 31,639. Static warehouse labels are display/search evidence, not dated PERMNO links. Request adjustment basis is unknown for some stored rows; the API preserves the stored adjusted-close field with explicit uncertified basis, rather than silently treating it as a certified adjusted price.

## Real ongoing collection

Existing `financial-system-data-plane` Worker version `e54b50a6-f0a6-46b3-a234-f753bd8ed69a`, collector commit `fcbca5577d94c8b2f701fb04c06a82271ad052a2` in `frc-ws-a`:

- Existing ten-minute weekday and 22:15 UTC weekday triggers are enabled independently of the legacy disabled acquisition mode.
- Event collection refreshes the active US-equity roster and rotates fairly across 14,412 accessible assets in 100-symbol cohorts. Provider trade events retain native event resolution, but arrive in scheduled batches. Recent IEX and at-least-16-minute-delayed SIP remain separate feeds. Forty upstream pages per invocation is a conservative configurable limit; whole-market event catch-up is not guaranteed. Per-cohort progress and lag remain explicit.
- Remote canaries and production-bound collection verified actual event pages in R2. Raw event prefix: `raw/alpaca/trades/continuous-v1/`; latest aggregate discovery receipt: `control/alpaca-continuous/v1/LATEST_RECEIPT.json`.
- All 122 daily batches completed and the ordinary publication quality/hash gates passed. Fifty-five failed zero-row attempts were recovered after fixing the current-day SIP request cutoff; completed batches were not reacquired. Daily target set remains the separate 12,134 registered identities, not the entire fresh 14,412-event roster.
- Acquisition is deployed TypeScript and does not require a continuing Codex agent session.

Daily publication release: `a5d8c4c23fe64a6fa2bdccb0e8ecf10f46dac0b03c5a51b1b82031a0abd86f7f`.
CRSP index release: `5a99de3beb22767c59d540212016a8b08c17d997d43762e3bd8f34534aebd63d`.
Provider-history index release: `54f287c014b55477a40436fa83e78cc601f9356bf5819687c64f9f7db2b54efd`.

## Verification and preserved work

- Production CRSP acceptance traversed all 82 catalog pages, accounted for all 40,518 unique IDs and all declared indexed rows, and fetched actual prices with lineage for AAPL, an old delisted security and a security outside the earlier cohort. Price pagination, owner authentication and unsupported historical-as-of refusals passed. This is not a scan of every price field in all 110 million rows.
- Headless production browser passed: default CRSP catalog, 250 native 2025 CRSP rows, 500 Yahoo archive rows, latest daily price, source switching, date-filter clearing, private-memory clearing and zero JavaScript errors. No source price values or screenshots entered model output.
- 31 focused stock tests pass, including quoted ETags, native provider-label case/aliases, stable IDs and source isolation. DCF suite passed 1,103 tests plus 10 subtests before the final client-only source menu additions; both JavaScript fixture suites passed after those changes. Collector: 183 tests plus TypeScript/binding checks. WRDS exporter: 21 focused tests. Full Zion suite was interrupted with missing optional benchmark-fixture setup errors; no full-suite certification is claimed.
- Missing WRDS `crsp_a_stock.stocknames_v2` was preserved: 87,839 rows in nine immutable parts, each hash-readback verified, staged without changing unrelated CURRENT pointers. Exporter fixes/receipt commit: `3ea00236943bb99cad0268faf37b992a6a3c44de` in `Financial System-wrds-r2-foundation`.
- Ordinary daily CURRENT advanced through its existing verified production publisher. CRSP/provider indexes were immutable metadata publications; unrelated SEC, valuation and portfolio publication pointers were preserved. No new subscriptions or replacement cloud infrastructure were created.

Evidence directory: `/Users/scottthomasswitzer/Documents/Secret/artifacts/crsp-backbone-2026-10-19/`. Daily/event receipts: `/Users/scottthomasswitzer/Documents/Secret/frc-ws-a/artifacts/financial-research-canonical-v1/workstreams/continuous-acquisition/`. No private price payloads are committed in this checkpoint.

## Exact remaining work

1. Additional missing-only Yahoo/provider acquisition is not enabled by this work; existing archived observations are now served. Filling the entire 2026 gap and expanding beyond previously collected provider cohorts remain separate acquisition tasks.
2. Other WRDS collections remain available through the native four-store API; full global-equity, derivatives and cross-provider corporate-action normalization are unfinished. Actual probes for full OptionMetrics and CRSP 62 tables returned permission errors despite catalog privileges. Preserve those as entitlement gaps, not retries or fabricated data.
3. Align the daily registered target set with the fresh active-asset inventory using verified source identities. Add event-query/status presentation if wanted; events are currently discoverable in the private object API/store browser, not a global live tick chart.
4. Native historical observations and current/static provider labels do not establish publication-time vintage evidence or safe cross-provider historical identity. No new PIT/backtest/trading-readiness claim is authorized by basic price serving.
5. Disk was around 3 GiB free. Do not restart old high-memory exporters or duplicate healthy jobs; use bounded resumable preservation.

Use `deploy/cloudflare-query/thin/wrangler.owner-data-release.jsonc` for the shared source Worker and `wrangler.production.jsonc` in the DCF checkout. Preserve concurrent dirty SEC readiness files. The default source now deliberately resolves to CRSP; specify another source to query a provider archive or current daily publication.
