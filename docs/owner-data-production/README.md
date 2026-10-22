# Production owner data access — 2026-10-19

The existing production DCF application now exposes the shared private data service. Open https://dcf-valuation-engine.scswitzer.workers.dev/data and connect the existing owner key. The terminal credential file points to this same production origin. No provider acquisition, new subscription, new cloud resource, production CURRENT mutation, or research-record write was needed.

## Working interfaces

| Access | Production behavior |
| --- | --- |
| `/data` | Stock catalog/OHLCV, 805 registered releases, four-store folder browsing, source-pinned Parquet schema/ten-row previews, all-WRDS shortcut |
| `/api/data/*` | Read-only owner-authenticated source access, streaming original responses |
| `/private/v1/*` | Compatible owner API aliases for installed Zion terminal commands |
| Valuation | Native SEC observations plus exact Damodaran release selections; explicit owner authorization before private benchmark resolution |

The Data page keeps its key/records in tab memory. Its separate, explicit benchmark grant requires an app login and the owner key, expires after 15 minutes, and is revoked on disconnect/leaving the page. Use the valuation link in another tab. App login alone cannot read private source data. Production preserves its existing D1 database and publication pointers.

```sh
zion stocks --symbol AAPL --limit 5
zion datasets catalog --search crsp --limit 20
zion datasets objects --store research --prefix raw/wrds/crsp__dsf/ --limit 20
zion datasets schema --store research --path PATH_FROM_OBJECT_LIST
zion datasets rows --store research --path PATH_FROM_OBJECT_LIST --columns EXACT_NATIVE_COLUMNS --limit 10
```

`/api/data/stock-history` indexes all 2,629 symbols and all 856,633 physical normalized rows from the existing Alpaca archive. All original rows were reconciled across 93 unique source pages. Only a 1,091,103-byte metadata index was added; prices remain in their original source objects. Symbol coverage is uneven and includes old canaries/batch windows; this is not continuous 2016–2026 coverage or a current quote feed. Raw prices, source currency, source IDs, conflicts, and missing historical availability remain explicit. Price availability is independent of the valuation issuer cohort.

## Verification and gaps

Production schema access was verified for all 1,681 WRDS namespaces containing Parquet, with exact census ETag/size matches and metadata matching the shared-source audit. The remaining 52 prefixes have no stored Parquet object. This tests one actual file per namespace, not every partition or row. All four stores are connected; 930 WRDS namespaces fall outside the 805-release curated registry and remain discoverable via source-native folder access. No registration, financial mapping or quality result was invented for missing manifests.

Production row reads additionally passed for existing CRSP daily prices, primary security data, adjustment-factor and event tables: ten projected rows from each, source version pinned, values never printed into model/tool output. These are native source fields, not a certified consolidated price/action model.

The macro catalog still has 147 series and 412,116 nonnull observations. Of 77,969 stored blank cells, 31,934 precede the first nonnull value, 43,164 follow the last, and 2,871 fall inside observed windows. Boundary classification does not establish economic inception/discontinuation; the source must explain causal reasons. No calendar dates or missing values were invented.

Production native SEC → Damodaran → enterprise valuation was independently reconciled with Decimal to $0.001116343 absolute error. This was an explicit scenario, not a recommendation. Missing equity/per-share bridges stayed null. Public manual calculations still work, while anonymous private benchmark requests return 401.

Remaining work is semantic integration across source-native collections: historical security identity, corporate-action/adjustment reconciliation, derivatives contract normalization, and vintage/availability evidence. Broad byte/schema access does not establish those properties. Slack stock commands and the separate protected terminal custom-domain deployment were not changed by this rollout. Existing source Worker names ending in `staging` were reused behind the actual production app; a separate production source estate was not created.

Protected metadata receipts are under `/Users/scottthomasswitzer/Documents/Secret/artifacts/all-data-wiring-2026-10-19/`. They distinguish source audit, production audit, actual row reads, browser/terminal checks, and valuation arithmetic. Private terminal exports stay outside Git with mode 0600.

## Deployment continuity

Use `deploy/cloudflare-query/thin/wrangler.owner-data-release.jsonc` for this shared source release. It preserves the stock, coverage, history, claims, SEC-engine and readiness pins together. The existing dirty default config belongs to concurrent work and must not silently replace these deployed pins. DCF production uses its own `wrangler.production.jsonc`, original database and normal CURRENT behavior. Existing upstream credentials were corrected against actual authenticated requests; do not seed production from stale local `.dev.vars` files.
