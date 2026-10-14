# Unified private research and valuation

This sprint adds a separately pinned research release over the existing immutable SEC service. It connects verified public SEC financial history to authorized owner-only historical Alpaca prices at exact source-backed filing points. It does not expand the SEC universe or enable licensed datasets.

The research release is `358ea45de35af6003347dc163fe31eebd09988d1b3715c3386e5ab8c0dfee72e`; SEC remains `b39a670bad0670449256e59d38bf6b9600503449baded29c25e41d5fb368c6bb`. There are 31 metric-certification companions and 21 unambiguous historical issuer/price joins. The demo uses 20 companies from healthcare, banking, payments, retail, consumer goods, semiconductors and industrials; NIKE is an additional supported point.

The selected historical price archive primarily covers 2019. Provider publication times are unavailable, so these are retrospective joins rather than point-in-time trading data. Identity validity is limited to literal filing dates. Financial date-only availability follows the conservative next-day midnight New York policy. Common-stock and debt symbols are separated through the approved market manifest; multiple covered classes still fail ambiguously.

Seven authenticated GET routes add identity, overview, historical prices, versioned valuation, peers, combined screening and metric certification. See [terminal and API examples](terminal.md). Exact formulas expose source inputs and formula versions; nonterminating ratios retain integer numerator/denominator strings. Unsupported market capitalization, enterprise value and related multiples remain unavailable rather than borrowing weighted-average shares, missing components or unknown availability dates.

Read [PASS/FAIL acceptance evidence](acceptance-gates.json), [independent review and matched benchmarks](calculations-review.md), [release control](release-control.md), [entitlement and incident review](security-review.md), and [cost evidence](costs.md). Corporate-action integration, continuous ticker/lifecycle history, narrow Basin credentials and custom TLS remain separate gaps. Production CURRENT pointers and original archives are preserved.

Protected operator receipts and native owner-only exports are under `/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-research-sprint/`. Source prices must remain in authorized local research exports and out of model contexts.

A hash-pinned projection of 31 exact SEC catalog entries avoids repeatedly loading large universe shards for the demonstrated cohort. Each projected entry retains its original shard reference, materialized-object hash, source member, source release and coverage limits. The runtime validates these bindings and uses the unchanged native payload validator. Unindexed issuers use the original catalog path; no archive rewrite or read-budget increase is involved.

## Verified delivery

Active Worker: `be4243ee-f3d5-4920-89bd-ad2e06f16ae1`, Worker source `1af8cd0`. Later main changes are terminal/operator verification and documentation; the Worker source and configuration are unchanged. The live twenty-company gate passes with 249 exact calculations, 498 independently source-bound inputs and 20 original decimal price checks. The combined screen returns 17 matches and explicit missingness for three companies' required revenue histories.

The final live metric audit matches 2,388 observations and 179 accounting-certified observations across 31 companies, with 1,535 separate coverage-gap records. Three conservative accounting substatus refinements are independently reproduced using the current pinned certifier; they do not change values, primary statuses or source IDs. Every original strict failure and failed promotion is retained.

The terminal's 126 workflow checks cover all twenty companies, a six-period Home Depot valuation chart and a 252-session provider-symbol price chart. The screen rendering repair was first verified against its protected HTTP200 export and then with an additional real two-company API/table request. Corporate actions remain the one failed sprint acceptance criterion; seven criteria pass with their stated evidence limits.

Example using the existing local credentials:

```sh
zion research overview --cik 0000354950 --format table
zion research valuation --cik 0000354950 --metrics operating_margin --frequency annual --date-from 2014-01-01 --date-to 2019-11-26 --as-of 2019-11-26T00:00:00Z --format chart --output new-hd-margin.json
```

Use the verified Workers origin from the terminal guide while custom TLS remains unresolved. Export files must be new; keep owner market exports private.

Final local verification: 969 unit tests passed, three dependency/optional tests skipped, and 22 native operator tests passed. Lint and frozen v2 contract checks pass. [Verification receipt hashes](verification.json) bind the protected live evidence. The matched repeated cache-hit benchmark measured p50 679 ms and p95 1,807 ms; the combined twenty-company screen took 11.27 seconds, read 64 objects/33.0 MB, and returned 17 supported matches. See [benchmark details](benchmark-summary.md) for unchanged workload and cache/load qualifications.
