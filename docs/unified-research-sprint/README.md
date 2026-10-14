# Unified private research and valuation

This sprint adds a separately pinned research release over the existing immutable SEC service. It connects verified public SEC financial history to authorized owner-only historical Alpaca prices at exact source-backed filing points. It does not expand the SEC universe or enable licensed datasets.

The research release is `0a78d635c0f01a3c584ae344d3b86cc6e351a499a8770439140619de46854184`; SEC remains `b39a670bad0670449256e59d38bf6b9600503449baded29c25e41d5fb368c6bb`. There are 31 metric-certification companions and 21 unambiguous historical issuer/price joins. The demo uses 20 companies from healthcare, banking, payments, retail, consumer goods, semiconductors and industrials; NIKE is an additional supported point.

The selected historical price archive primarily covers 2019. Provider publication times are unavailable, so these are retrospective joins rather than point-in-time trading data. Identity validity is limited to literal filing dates. Financial date-only availability follows the conservative next-day midnight New York policy. Common-stock and debt symbols are separated through the approved market manifest; multiple covered classes still fail ambiguously.

Seven authenticated GET routes add identity, overview, historical prices, versioned valuation, peers, combined screening and metric certification. See [terminal and API examples](terminal.md). Exact formulas expose source inputs and formula versions; nonterminating ratios retain integer numerator/denominator strings. Unsupported market capitalization, enterprise value and related multiples remain unavailable rather than borrowing weighted-average shares, missing components or unknown availability dates.

Read [PASS/FAIL acceptance evidence](acceptance-gates.json), [independent review and matched benchmarks](calculations-review.md), [release control](release-control.md), [entitlement and incident review](security-review.md), and [cost evidence](costs.md). Corporate-action integration, continuous ticker/lifecycle history, narrow Basin credentials and custom TLS remain separate gaps. Production CURRENT pointers and original archives are preserved.

Protected operator receipts and native owner-only exports are under `/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-research-sprint/`. Source prices must remain in authorized local research exports and out of model contexts.

A hash-pinned projection of 31 exact SEC catalog entries avoids repeatedly loading large universe shards for the demonstrated cohort. Each projected entry retains its original shard reference, materialized-object hash, source member, source release and coverage limits. The runtime validates these bindings and uses the unchanged native payload validator. Unindexed issuers use the original catalog path; no archive rewrite or read-budget increase is involved.
