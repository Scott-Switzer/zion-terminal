# ADR: restricted financial SQL foundation

Status: isolated public pilot; no canonical or licensed dataset promotion.

Use Basin SQL for registered Iceberg tables and retain the immutable native Parquet service for existing archives. Do not silently switch engines. The fixed-template adapter lives behind the existing authenticated private service at `/private/v1/datasets/financial-query`. The public Catalog entrypoint cannot invoke it. A server-assigned private-owner principal, content-hashed policy, fixed tables, fixed SQL, exact snapshot/metadata pins and pre/post execution checks define the permitted query.

| Option | Demonstrated capability | Limitation / decision |
|---|---|---|
| Basin SQL + Catalog | CTE, window revision selection, LEFT JOIN, JSON EXPLAIN, exact DECIMAL arithmetic on isolated registered tables | Existing raw Parquet is not automatically registered Iceberg. Adopt only explicit permitted registrations. |
| Native Parquet | Existing authenticated schema/rows routes preserve original archives and source types | Per-file access is not a general multi-table analytical engine. Retain existing routes. |
| DuckDB | Independent public pilot join and decimal verification, one thread, 64 MB, external access disabled | Local validation only; no deployed fallback, no automatic licensed downloads. |

The pilot has two issuer CIK anchors, six public SEC concept revisions and one owned precision/null canary. It selects three annual source contexts for one exact customer-contract revenue concept, as of 2026-01-01. Availability is filtered before revision ranking. Date-only filings become available at the following New York midnight, a declared conservative policy. These are not historical ticker links, a complete issuer history, or general total-revenue certification. A missing concept produces an explicit coverage row, never a financial zero or invented source-null observation.

Raw, normalized and derived observations have distinct semantics. Versioned contracts live under `contracts/financial-identity-link-v1.schema.json`, `fundamental-observation-v1.schema.json` and `financial-mapping-v1.json`. RAW allows unknown identity/metric mappings. Normalization requires established mappings. Identity links require historical validity and source availability, rather than present labels. Derived results require a separate formula contract. The Python helper performs partial semantic checks; complete schema and entitlement enforcement is required before future canonical integration.

Budgets: 1 MiB declared source files and actual scanned bytes, 100 returned rows, 256 KiB response, 15 second caller deadline. SQL requests LIMIT n+1 and reject overflow. Timeout bounds the caller but does not prove engine cancellation or cancel pending R2 operations. Read-only SQL is constructed on the server; callers cannot supply SQL, identifiers outside the pilot, tables, hosts, credentials or recipient=llm. Cache keys include policy, principal and full plan; source versions are checked on cache hits.

Metadata canonicalization currently covers the demonstrated integer/ASCII pilot metadata, with snapshot lists sorted by ID. Before accepting general metadata, standardize Python/JavaScript Unicode and numeric lexical serialization and test it across languages.

Security limitation: the existing credential successfully reads SQL/catalog and created the isolated pilot; its narrowly read-only scope was not verified (permission-group inspection denied). The runtime principal is a single private owner, not a multi-user entitlement system. Licensed access remains disabled in this adapter. Existing owner-only raw routes have not been reclassified as safe LLM inputs. Storage readability is not migration or redistribution permission. Require a separately verified least-privilege credential and per-source rights before expansion. No production publication pointer may be changed without a separate approved gate.

Sources verified during implementation:
- [Basin SQL querying](https://developers.cloudflare.com/basin-sql/query-data/)
- [SQL reference](https://developers.cloudflare.com/basin-sql/sql-reference/)
- [Limitations](https://developers.cloudflare.com/basin-sql/reference/limitations-best-practices/)
- [Catalog management](https://developers.cloudflare.com/basin-catalog/manage-catalogs/)
- [Apache Iceberg specification](https://iceberg.apache.org/spec/)
- [Iceberg REST specification](https://iceberg.apache.org/rest-catalog-spec/)
- [Basin pricing](https://developers.cloudflare.com/basin-sql/platform/pricing/)

Costs: Basin bills compressed bytes scanned, with a 10 MB query minimum, 10 GB monthly included and $0.0025/GB beyond that allowance. A minimum-billed query is approximately $0.000025 before allowance; 1,000 such queries approximately $0.025. Account remaining allowance, R2/catalog operations, Worker requests and storage costs are not verified here. These estimates are not a claim of zero total cost. The measured pilot is 11,913 physical source bytes before any source-version test; compare execution metrics in the acceptance artifact, not file size alone. Metadata-only EXPLAIN has no scan charge. Repeated cache hits still perform metadata validation and R2 operations.
