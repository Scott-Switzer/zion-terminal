# Bounded public SEC layout experiment

`sec_basin_optimize.py` evaluates the existing 341-file hash16 CIK/year table against two isolated alternatives containing the exact same rows: compact sorted unpartitioned, and sorted hash16 CIK without year fragmentation. It does not modify the source table, runtime configuration, credentials, or current release pointers. Only existing operator credentials and catalog-vended storage access are used.

Cloudflare's current [Basin SQL best practices](https://developers.cloudflare.com/basin-sql/reference/limitations-best-practices/) (updated October 1, 2026) support partitioned and unpartitioned tables, recommend compaction, projected columns, time filters, and EXPLAIN for predicate pushdown. The [April 20 changelog](https://developers.cloudflare.com/changelog/post/2026-04-20-r2-sql-json-functions-explain-format/) confirms EXPLAIN FORMAT JSON and unpartitioned support. The [query engine deep dive](https://blog.cloudflare.com/r2-sql-deep-dive/) describes metadata and partition-statistics pruning; that architecture does not establish that a particular bucket/truncate expression is pruned in the current endpoint. Actual plans and scan counters must decide.

Evidence is written to `/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale/basin-optimization.json`. Each alternate table is independently read back and compared using a duplicate-preserving canonical SHA-256 over every field, including nulls and string decimals. All benchmark result pairs are compared exactly against the baseline rows. Each layout runs three source query cases with three latency samples and a real JSON EXPLAIN. HTTP plan errors are retained, never synthesized as plans.

The guard stops before exceeding 50 SQL requests or a projected 1 GB cumulative SQL scan. Writes require a local compressed estimate with 2x headroom plus 5 MB metadata allowance under the 200 MB persistent-data cap; committed file sizes plus allowance are checked afterward. Actual catalog data-file byte counts are preserved. Metadata allowance is conservative accounting, not an object inventory. The script fails closed for missing execution scan metrics. Catalog reads for full-row parity are separate from SQL scan counters.

First observed and repeated observed executions are reported separately with cache metrics. The operator cannot flush Cloudflare caches, so these are not proven cold/warm cache trials. Three samples yield descriptive nearest-rank p50/p95, not stable tail-latency estimates. Sorting is physical append order, not a declared Iceberg sort-order guarantee for future writers. No production promotion or runtime least-privilege credential validation is included.

## Executed result

The run completed with 36 SQL requests and 475,686,180 measured SQL scan bytes. New data files total 56,756,477 bytes; conservative metadata allowances bring accounted persistent data to 66,756,477 bytes. All nine query/layout combinations matched exact result pairs; both complete alternative tables matched all 500,965 original rows and fields. All nine real EXPLAIN requests succeeded and are retained in full.

| Layout | Data files | MSFT revenue p50/p95 ms | Two issuer p50/p95 ms | Three issuer p50/p95 ms |
|---|---:|---:|---:|---:|
| Existing hash16/year | 341 | 1489 / 2130 | 961 / 1609 | 1351 / 2820 |
| Compact sorted | 2 | 3841 / 4850 | 3858 / 4584 | 2099 / 2657 |
| Hash16 sorted, no year | 16 | 1003 / 2327 | 925 / 1260 | 1295 / 1345 |

The 16-file alternate reduced file/request overhead and had modestly lower observed median latency. It increased scan bytes for the first two cases: MSFT 13.89 MB to 20.97 MB, two issuer 7.07 MB to 21.08 MB. Compact sorted reduced file requests but was slower. Neither alternate dominates the baseline. Keep them isolated for reproducibility; do not change the production layout on these three samples. A useful layout correction is now concretely available as the 16-file isolated table, with demonstrated request-overhead reduction and exact parity, but its scan-byte tradeoff needs a workload decision before promotion. File-scan counters can exceed physical table files because execution includes metadata and late-materialization activity; do not equate those counters with unique parquet objects.

The observed bucket16 plans selected all 16 data files for every case (28,421,558 planned bytes), so the CIK predicate did **not** demonstrate bucket pruning at the planning stage. Existing hash16/year plans selected 190, 112, and 270 data files respectively, versus 341 physical files total. This is evidence of some baseline pruning, not proof of which individual transform contributed. Predicate presence in FilterExec alone does not prove file pruning.

## Explicit identity-group candidate

`python scripts/sec_basin_optimize.py --identity` is a separate one-shot continuation, refusing an existing `basin-identity-optimization.json`. It loads the prior budget and permits at most 12 additional SQL calls (48 total). It adds `cik_group = zlib.crc32(cik.encode('ascii')) % 16` as an integer, partitions by this column with Iceberg identity, and retains sorting by CIK/metric/end/source ID. The operator SQL adds the exact group constants for the requested CIKs alongside the original CIK predicate. Group collisions are safe because the original predicate remains. Every original column must retain full duplicate-preserving parity; the added column is checked independently against its deterministic definition.

The identity candidate executed successfully: all 500,965 original rows/fields match exactly, and all group values match the deterministic definition. It has 16 files and 28,422,693 data bytes. Cumulative accounting across all three new layouts is 100,179,170 persistent bytes including allowances, 48 SQL requests, and 508,894,581 SQL scan bytes.

| Case | Existing scan files / MB | Identity scan files / MB | Existing p95 ms | Identity p50 / p95 ms |
|---|---:|---:|---:|---:|
| MSFT revenue | 207 / 13.89 | 6 / 1.97 | 2130 | 1845 / 1953 |
| Two issuer | 131 / 7.07 | 7 / 3.70 | 1609 | 1768 / 2490 |
| Three issuer | 325 / 21.75 | 8 / 5.39 | 2820 | 2060 / 2578 |

Explicit identity-group filtering fixes the demonstrated pruning failure for these queries and reduces scanned bytes/files in every case. However, the two-issuer p95 regresses and all three medians are slower in this small sequential sample. The strict all-case correctness/files/bytes/p95 selection gate therefore fails. Retain the implemented identity layout and operator predicate as isolated, verified candidates; no serving default or production promotion is justified. The remaining limitation is latency, not unproven partition pruning. Full actual JSON plans and per-request cache counters are in `basin-identity-optimization.json`; the cumulative 50-request limit leaves only two SQL requests, insufficient for another complete benchmark suite.

Actual identity plans select 1, 2, and 3 data files respectively (DataSourceExec and LateMaterializationExec), matching the requested coarse groups rather than all 16 partitions. This directly establishes planner pruning; higher execution `files_scanned` counters remain separate from unique data files.
