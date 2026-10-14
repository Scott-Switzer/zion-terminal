# Bounded universe screening

`sec_universe_screen.py` serves the `screening_metrics` manifest format
`sec-screen-metric-shards-v1`. Each metric references hash-pinned chunks with
`key`, `sha256`, and preferably exact serialized `bytes`. Chunks retain the
existing company-row shape filtered to that metric and pin the root release ID
and registry hash. The immutable `sec_screener.py` remains the fallback for
manifests with no metric shards.

The publisher must sort globally by `(missing, exact value, CIK)` ascending,
placing missing values last. Descending requests traverse chunks and rows in
reverse, so equal-valued CIKs also descend. The response states this ordering.
Every read verifies global ordering and rejects duplicate CIKs or changed
release/registry context. Filters and pagination use exact rational arithmetic.
Only the selected page is retained; all matches are counted. Missing companies
retain their reason instead of entering the numeric filter as zero.

Limits: 32 chunks, 2 MiB per chunk, 32 MiB total metric reads, 25,000 materialized
CIKs, 100 page matches, 2 MiB response, and 20 seconds. Declared shard byte counts
are checked before reading; unknown counts conservatively cost 2 MiB each.
A requested CIK absent from all shards fails with 404 after the scan. Signed
cursors bind query and release; changing either fails with 409. The fixed cutoff
must equal the root's maximum as-of timestamp. Auth and lexical threshold guards
apply before data reads. No archive scans, SQL, or price sources run interactively.
