# Expanded stored SEC financial coverage

The private API can read the existing September 24, 2026 CompanyFacts archive
without downloading or extracting the bulk ZIP. A hash-pinned sharded index
references 19,863 bounded filer members and 8,419 stored ticker mappings covering
6,545 distinct issuers. These are discoverable source counts, not a claim that
every filer has every metric or is a currently listed US operating company.
533 oversized/unsupported members are excluded from this new bounded path;
existing canonical serving remains available separately.

Authenticated requests to `POST /private/v1/query`:

```json
{"operation":"sec_coverage"}
```
```json
{"operation":"sec_financials","symbol":"UBER","metrics":["revenue","net_income","total_assets","operating_cash_flow"],"period":"annual","lookback":10}
```
```json
{"operation":"sec_financials","cik":"0001543151","metrics":["revenue"],"period":"annual","lookback":5,"as_of":"2023-01-01T00:00:00Z"}
```

Choose exactly one ticker or CIK. Ticker links come from an existing stored SEC
snapshot with unknown availability date; historical requests therefore require
CIK, not a fabricated dated ticker mapping. `as_of` is a nonfuture timezone-aware
instant. Without it, the response is the latest eligible data in the stored
September 24 snapshot, with the resolved cutoff disclosed.

Twenty explicit metrics are available where their native US-GAAP concepts exist.
Queries accept 1–20 distinct metrics, annual/quarterly periods, and 1–40 periods
per metric. Currency values require USD; shares and EPS use their specific native
units. No FX conversion, YTD-to-quarter conversion, extrapolation or missing-zero
substitution occurs. Annual durations must be 330–380 days; quarterly durations
75–105 days. Instant values align to available duration-period anchors.

Each observation identifies its source concept, unit, fiscal dates, accession,
filing date and conservative availability policy. Date-only filings become
eligible at the next New York midnight, not at ingestion time. Revisions are
filtered before selection. Conflicting same-availability values are retained as
conflicts instead of averaged. Selection prefers documented source concepts;
sector equivalence and independent financial validation remain unestablished.

Archive reads are ETag-pinned ranges. Decompressed members are capped at 4 MiB,
compressed members at 2 MiB, and returned/persisted query results at 1 MiB.
ZIP member CRC/size and computed member SHA-256 are checked. The full 1.4-GB
archive digest is declared provenance and was not rehashed during indexing.

Normalized query results persist in the existing financial R2 store, keyed by
source release, CIK, metrics, period, cutoff and normalization version. Cache
reads verify the stored digest and release; request IDs are fresh. No provider
requests, WRDS acquisition, legacy pointer changes or public raw-data routes are
introduced. Access to this path remains behind the private bearer authentication.
