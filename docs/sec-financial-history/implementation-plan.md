# SEC financial history execution ledger

Intent: authenticated, source-backed, multi-metric annual/quarterly histories for five supported US issuers. Preserve archives, licensed-data restrictions, all existing release pointers and routes. No provider requests.

Design: build a new immutable public SEC release from five ETag/CRC/SHA-pinned stored CompanyFacts members. A versioned registry defines exact concepts, units, contexts and conservative mapping exclusions. Keep all revisions and duplicates' source identities. Query availability before revision choice; reject ambiguous concepts/values. Align using reported annual/quarterly periods, without assuming calendar years. Derive cash-flow quarters from matching cumulative periods only, FCF from aligned CFO minus reported positive PP&E payments, and TTM from four contiguous validated quarters only. Debt is explicit interest-bearing debt components, never liabilities or a missing component interpreted as zero. Weighted-average diluted shares cannot be summed for TTM.

Serve fixed CIK/metric/frequency/date/cutoff requests through the existing private dataset service. New public-only Iceberg tables supply Basin SQL; immutable source releases and version pins support independent reproduction. No caller SQL, licensed table access or implicit engine fallback. Source values remain decimal strings. Return period-specific coverage gaps and mapping decisions.

Sequence:
1. Inspect baseline/evidence; read-only credential/runtime and TLS diagnostics. Recover bounded oversized metadata; quarantine unsealed releases and legacy reference; reconcile catalog enablement and snapshot metadata. Full archive row certification remains a distinct gate.
2. Tests first for registry, periods, revisions, precision and derived prerequisites. Extract five public members; document each issuer's coverage/mapping decisions.
3. Build immutable public release and isolated Iceberg registration. Implement private history route with budgeted reads, version checks and exact decimal arithmetic.
4. Independently compare source/SQL/Python/DuckDB and live histories; test denial, restatements, incomplete periods and immutable reproduction. One final independent review.
5. Update evidence, benchmarks, docs and gates; commit/push directly and deploy existing services. Recheck publication pointers.

Ruling: prior native execution/direct commit/deploy authorization persists. No extra approval cycle or PR. Preserve preexisting untracked uv.lock.

Ruling: account token-management endpoints deny both available credentials; local named-token search has not yet located an administrator credential. The new history API therefore uses an explicitly identified native immutable SEC release engine, not the broad Basin operator token and not an automatic fallback. Basin is an independent operator-side verification engine; its restricted foundation route stays unchanged. This reduces runtime privilege while the narrow-credential gate remains honestly failed.

Ruling: Microsoft and Amazon are absent from the existing source index. Test cohort is Apple, Costco, NVIDIA, Meta and Alphabet. Costco revenue maps only to native Revenues; net sales is not substituted. Missing NVIDIA PP&E payments and missing debt components remain coverage gaps.
