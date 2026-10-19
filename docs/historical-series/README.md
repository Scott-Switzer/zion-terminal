# Historical coverage expansion — October 19, 2026

The existing Zion source API and installed terminal now serve official historical yield, FX and commodity observations. This extends the existing platform and R2 data plane; no new app, paid subscription, cloud resource or production pointer was introduced.

## Acquired and live verified

| Family | Series | Reported values | Explicit missing cells | Source period |
| --- | ---: | ---: | ---: | --- |
| Treasury nominal and real par yields | 19 | 127,408 | 1,193 | 1990-01-02–2026-10-16; real yields begin2003 |
| ECB currency-per-EUR reference rates | 41 | 221,441 | 70,356 | 1999-01-04–2026-10-19 |
| World Bank commodity prices and indices | 87 | 63,267 | 6,420 | 1960-01–2026-09 |
| Total | 147 | 412,116 | 77,969 | 63 source files |

These are series and source cells, not 147 securities or every historical asset. The source grid contains490,085 cells. The ECB archive retains discontinued currencies; nulls after a currency disappears are preserved. Catalog date ranges describe source rows, including missing cells; they do not prove every date has a value. World Bank first-of-month dates identify monthly periods, never publication instants. Commodity indices retain their actual index units and are not futures prices. Treasury observations are percent yields, not bond prices.

Official sources: [Treasury](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve), [ECB](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), [World Bank](https://www.worldbank.org/en/research/commodity-markets).

## Existing interfaces

```sh
# Search all available historical series.
zion historical --search gold
zion historical --asset-class rates --limit 25

# Source-qualified observations with preserved decimal values and units.
zion historical --series-id ecb:fx:USD:EUR \
  --date-from 1999-01-01 --date-to 1999-01-08 --limit 5

zion historical --series-id us_treasury:nominal:10y \
  --date-from 1990-01-01 --date-to 1990-01-31 --limit 25

# Inspect actual source provenance and separately labelled discovery candidates.
zion historical --view sources
```

Credentials reuse `~/.config/zion/private-api.env`; no key is accepted in a command argument. Use `--output /path/to/new.json` for an exclusive JSON export. Pagination uses the returned `next_cursor` plus `--expected-release-id` and the same filters/limit; no source release is silently substituted.

Authenticated staging API:

- `GET /private/v1/historical-series?search=gold&limit=25`
- `GET /private/v1/historical-series?series_id=us_treasury:nominal:10y&date_from=1990-01-01&date_to=1990-01-31`
- `GET /private/v1/historical-series?view=sources`

Origin: `https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev`.

The existing SEC readiness/history/claims, dataset catalog and DCF interfaces are preserved. Historical-series access is implemented in API and terminal. Slack/MCP remain on their previously verified readiness path; no new Slack historical-series command or message delivery is claimed. The existing DCF models do not automatically substitute these series for financial inputs or benchmark assumptions.

## Bottlenecks removed

- Complete supported source histories replace hardcoded one-year/sample pulls:37 nominal Treasury years,24 real Treasury years, the complete ECB archive and the complete World Bank monthly workbook.
- Independent source tasks run concurrently with a fixed small worker budget. A failed task is recorded without discarding completed checkpoints or preventing unrelated source progress.
- Every cached raw file is rehashed before reuse. A real second run reused all63 source files with **zero downloads**. Explicit refresh ignores stale probe seeds.
- Raw objects are content addressed. Normalized series and the manifest are immutable and source pinned. Publication preflights all local hashes/lengths, reads every remote object back against the expected pin, and uploads the manifest last. The published release contains211 objects and27,986,012 bytes. CURRENT pointers are never written.
- Queries read only a bounded manifest and the requested series. There are no query-time upstream provider calls, full object-store scans or LLM dependencies. Responses are bounded and paginated; cursors bind the exact release and filters.
- Missing, malformed, conflicting and nonfinite values cannot silently become zero. Four actual World Bank spreadsheet error cells are explicit `SOURCE_CELL_ERROR` nulls. Original bytes, units, hashes, source IDs, retrieval metadata and missing reasons remain inspectable.

No continuous schedule was enabled. Cached snapshots remain pinned until an explicit acquisition refresh publishes a new candidate.

## Historical availability and rights

Current downloads of old observation dates do not prove which version existed at an earlier decision date. These series therefore use `point_in_time_status=NOT_ESTABLISHED`, `available_at=null` and `historical_as_of_supported=false`; an `as_of` request returns422. They cannot automatically enter historical backtests or certified PIT valuations.

Access is owner authenticated and private. Source-specific attribution, third-party rights exceptions and redistribution review remain visible; public download availability is not a blanket redistribution licence. No institutional/licensed data was copied into this release or committed to Git.

## Further data found online

`config/historical_sources.json` distinguishes implemented adapters, existing services and discovery-only candidates. Discovery does not count as acquired coverage.

- [BIS bulk data](https://data.bis.org/bulkdownload): credit, debt securities, property prices and FX.
- [Federal Reserve GSW curves](https://www.federalreserve.gov/data/nominal-yield-curve.htm): estimated historical curves from1961, explicitly model estimates.
- [Binance public archives](https://github.com/binance/binance-public-data): venue-specific crypto/derivatives history; checksums, corrections and the2025 timestamp-unit change need validation.
- [CFTC historical reports](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm): positioning, with reporting and release dates kept distinct.
- [NBER Macrohistory](https://www.nber.org/research/data/nber-macrohistory-database): long historical economic series with source-specific conventions.
- [ECB SDMX](https://data.ecb.europa.eu/help/api/data) and [IMF API](https://data.imf.org/en/Resource-Pages/IMF-API): broader official macro/credit/rate data with revision handling still to implement.
- [JST Macrohistory](https://www.macrohistory.net/database/): long-run country aggregates covering equities, bonds, housing and macro. Its [licence](https://www.macrohistory.net/database/licence-terms/) and download page prohibit commercial-provider integration. This remains **DISCOVERED_RESTRICTED_NOT_INGESTED**. A downloaded raw candidate and licensing evidence remain outside Git; no adapter, integration or publication was made.
- Existing Zion SEC and private dataset services remain the paths for company/fund filings and institutional historical assets. They are not duplicated by this expansion.

Individual historical securities, delistings, corporate actions, class identities, exchange-specific futures/options contracts, global funds, private assets and unsupported jurisdictions are not certified complete. Asset-class indices are not substitutes for individual assets.

## Reproduction and deployment

```sh
.venv/bin/python scripts/acquire_historical_sources.py \
  --directory /path/to/local/history-store

# Explicit refresh. Existing snapshots remain available.
.venv/bin/python scripts/acquire_historical_sources.py \
  --directory /path/to/local/history-store --refresh

# Existing private bucket, immutable objects only, full readback; no pointer changes.
.venv/bin/python scripts/acquire_historical_sources.py \
  --directory /path/to/local/history-store --publish

.venv/bin/python scripts/verify_historical_sources_live.py \
  --directory /path/to/local/history-store --output /path/to/new-acceptance.json
```

The actual local store is `/Users/scottthomasswitzer/Documents/Secret/artifacts/historical-expansion-2026-10-19/store`. Raw source data and complete exported responses remain outside Git. Metadata-only receipts are in `receipts/` beside this file.

Pinned release: `024d7a0ec03fad494eaf5698599b9c8185068bc6dd3bf3b78a3a4bd7d727d62c`.
Manifest SHA256: `6b3f48fd9f27ec2ded4d128c3404959bbf3df725d17562c049eeb262c84f61c9`.
Staging Worker version: `baed428c-4a19-4b3e-8d7b-fb6f7e5466b0`; deployed source6e2d773.

`deploy/cloudflare-query/thin/historical-staging.vars.json` contains only the additional immutable history pins. Deployment used a temporary overlay preserving the previously deployed50-member SEC claims configuration; concurrent dirty readiness/sector config files were not overwritten or committed. Production source publication pointers are unchanged.

Verification: **78 focused tests passed**; **1,254 non-benchmark tests passed,3 skipped**, one existing warning. The11 previously known benchmark-fixture setup errors are outside that green run. Ruff, diff checks and deployment dry run passed. Live acceptance verified source catalog counts, three series with pagination, explicit null handling, auth/PIT/wrong-release refusals and the existing expanded equity-claims source. Installed terminal returned five exact1999 ECB observations, beginning with USD/EUR1.1789. An independent review found publication integrity and identity issues; fixes passed regression tests and re-review before publication.

Commits:5dc4e23 Treasury; b865a63 ECB;1afe92e World Bank; cc869d1 acquisition/storage/publication; e7881ad API/terminal;6e2d773 pins/live acceptance. Commits remain local. No production deployment, production pointer promotion, paid acquisition, Slack message or public Git publication was performed.
