# Full-universe Basin identity layout: executed evidence

The isolated full-universe Basin materialization and SQL verification **passed** on October 13, 2026. It contains **8,576,461 canonical revisions for 15,136 usable issuers**, stored as 16 sorted identity-group fact files and one issuer file. The native serving path remains primary; no CURRENT pointer, runtime credential, Worker bundle, or serving default was changed by this workstream. Additional full-company financial certifications remain zero.

## Source and exact parity

Materialization run: `c1fae45f2684fc29e5aaca95c29a18ffa5a769376ba234c6686206ea1167080a`. Native publication: `0f0fa0be29d822e3b25e913239845c4702cddbfb7f60d2e0cf1661a8cea1a388`. The complete native receipt accounts for 20,396 attempted issuers, including 5,260 quarantined issuers excluded from these tables.

Source gates verified the native serving-manifest SHA, PUBLIC_SEC_ONLY policy with licensed sources disabled, exact complete checkpoint counts, the complete checkpoint-set hash reproducing the native publication identity, PASS company verification, immutable company object keys, source object SHA/length, member hashes, and summed normalized revision counts. Every company's source payload was read once through the existing native R2 helper with four bounded fetches. No raw archive was downloaded or modified.

The original 15-column `canonical_revisions` projection is preserved, including nulls, exact string decimals, source-record JSON and lineage. The additional integer column is `cik_group=CRC32(CIK)%16`; its values are checked separately. Sorted CIK/metric/end/source-ID/full-row ordering supports sequential duplicate-preserving source-projection SHA/count comparison for every group. Local readback, full remote file-byte hashes, remote original-field row hashes/counts, deterministic groups, catalog file paths and snapshot totals all passed. The issuer table has exactly one row per usable CIK.

Namespace: `zion_sec_universe_c1fae45f2684_identity16`.

| Table | Rows | Files | Data bytes | Snapshot |
|---|---:|---:|---:|---|
| canonical_revisions | 8,576,461 | 16 | 472,603,704 | `3494557472409119475` |
| issuers | 15,136 | 1 | 823,312 | `8383757386293381114` |

## Actual SQL evidence

Two disjoint identity-group halves verified every CIK/count pair exactly: 7,673 plus 7,463 issuer rows, covering all 15,136 issuers and all 8,576,461 revisions. Each half filters the group on both sides of a CIK-equality join and uses expected count plus one as LIMIT. This respects the documented [10,000 LIMIT maximum](https://developers.cloudflare.com/basin-sql/troubleshooting/#invalid-limit-values); oversize halves fail rather than truncate. The bounded join and two small financial queries also matched their source-derived expected results exactly.

| Query | Returned rows | Planned fact files | Execution scan-file counter | Scanned bytes | Observed latency ms |
|---|---:|---:|---:|---:|---:|
| Full join, groups 0–7 | 7,673 | 8 | 19 | 639,748 | 5,809.07 |
| Full join, groups 8–15 | 7,463 | 8 | 19 | 618,754 | 6,701.12 |
| MSFT revenue | 182 | 1 | 6 | 29,408,793 | 3,816.02 |
| MSFT issuer join | 1 | 1 | 12 | 161,101 | 1,733.77 |
| Two issuers / net income + cash | 226 | 2 | 7 | 58,473,480 | 5,242.59 |

All five actual JSON EXPLAIN plans and all five executions succeeded. The explicit identity-group predicates selected eight files per full-universe half, one file for MSFT, and two files for the two-issuer case. Execution `files_scanned` counters include metadata/late-materialization activity and are not unique Parquet file counts.

These are single observed executions with cache counters recorded; they establish correctness and actual pruning, not cold/warm p50 or p95. The larger full-universe workload is not a matched latency comparison with the earlier 441-issuer experiment. The earlier pilot's two-issuer p95 regression still does not justify promoting a Basin serving default.

## Record-quality accounting

Counters below use already-required source payload reads and are labeled **INSPECTED_NOT_FINANCIALLY_CERTIFIED**. They cover usable company payloads; quarantined issuer payloads are a separate category. Raw inventories include unmapped concepts, while rejection counts describe mapped concept source rows only. Unmapped concepts are not silently accepted, rejected or certified and remain available in the immutable source archive.

| Inspected source measure | Count |
|---|---:|
| Raw source records | 119,784,879 |
| Candidate US periodic records | 118,202,926 |
| Mapped record rejections | 106,377 |
| Unsupported form | 89,398 |
| Unsupported native unit | 16,476 |
| Period end after source filing date | 503 |
| Financial-statement-evidence policy family | 1,738 companies |
| General US GAAP unclassified policy family | 13,398 companies |
| Missing source inventories | 0 |

## Resource and credential constraints

New data totals **473,427,016 bytes**, below the 512 MB data cap. Measured retained metadata, including current/logged metadata JSON, manifest lists and manifests, totals **28,597 bytes**, below the 50 MB reserve. Preparation retained a 600 MiB free-disk reserve plus compressed pending-write headroom and produced no per-company Parquet files. Original archives remain untouched.

The new SQL allocation used **10 calls and 89,301,876 scanned bytes**, below its 768 MB cap. Including the completed prior experiments, cumulative Basin SQL accounting is **58 calls and 598,196,457 scanned bytes**. Historical experimental caps/evidence were preserved separately. Reservations used actual source-plan bytes, doubled only for late materialization, plus metadata headroom; missing metrics/estimates fail closed.

The as-run registration preflight recorded a **$4.990771 fractional-storage estimate**, including native retained bytes, prior Basin accounting, the full 512 MB plus 50 MB new reserve, concurrent fresh-publication 200 MB reserve, and operation/SQL allowances under the $5 task maximum. Final budget reconciliation corrects [R2 whole-unit storage rounding](https://developers.cloudflare.com/r2/pricing/): 7,384,736,954 reserved bytes require **8 GB-months**, so the final reserved ceiling is **$5.00 exactly** (`4.50 + 0.36 + 8 × 0.015 + 0.02`), within the authorized maximum. Exact decimal arithmetic prevents floating-point boundary errors. As-run receipts were preserved unchanged; `final-budget-reconciliation.json` records the correction and receipt hash. No cloud writes, SQL calls, or source reads were repeated. This is a bounded task reservation, not an observed account bill; unrelated account usage and base subscriptions are excluded. [Current Basin pricing](https://developers.cloudflare.com/basin-sql/platform/pricing/) is additive to R2/catalog charges, uses $0.0025/GB with a 10 MB query minimum, and excludes EXPLAIN from SQL scan billing.

Preparation used the existing FRC native R2 client; registration/validation used PPE's existing Basin operator token and catalog-vended `table.io`. No credentials were copied or expanded. Files were registered through documented [PyIceberg add_files](https://py.iceberg.apache.org/api/#add-files) with identity statistics, automatic name mapping and duplicate checks enabled. Unsupported inference/IO or existing snapshots stop rather than silently fall back to another write path. Registered files are isolated table-owned assets; destructive table maintenance is outside this task. Runtime least-privilege credential validation remains unverified.

## Reproducible implementation and evidence

Implementation: `scripts/sec_universe_basin.py`, explicit `prepare`, `register`, and `validate` stages. The `--release` argument is the materialization run identity, not the separately derived native publication identity. Stages refuse existing evidence/progress to prevent silent budget resets or duplicate writes.

Evidence directory: `/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-universe-sprint/basin-prepared/`.

- `prepared.json`: full source/local parity, per-group hashes/counts, issuer source rows, record-quality totals.
- `registered.json`: actual tables/snapshots, remote parity, metadata sizes and stricter spend preflight.
- `sql-validation.json`: complete real plans, exact SQL comparisons, latency/cache/scan metrics and cumulative budgets.
- `final-budget-reconciliation.json`: protected final whole-unit $5.00 reservation and preserved as-run receipt provenance.

Seventeen focused tests passed, covering exact decimals/duplicates/lineage, source-publication hash tamper detection, source policy/completeness, synthetic Parquet preparation, installed identity footer inference and mixed-group rejection, disk/query limits, disjoint full-universe coverage without truncation, and exact whole-GB rounding (8 GB fits $5; 9 GB exceeds it). Ruff formatting and checks passed. Full-repository verification is owned by the parent integration task.
