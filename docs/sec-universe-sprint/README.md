# SEC universe execution sprint

The existing public SEC archive is processed with one financial calculator and four bounded publication I/O tasks. A three-block 4MiB range cache avoids extracting or downloading the entire ZIP. Each member is checked against pinned ETag, local ZIP header/name, size, CRC and member SHA; the full archive SHA remains declared, not independently rehashed.

`python scripts/sec_universe_pipeline.py --maximum-spend 5` resumes gzip checkpoints and automatically passes 2,000, 5,000 and full-discovery stages. Independently reconciled Python/DuckDB outputs are required before company payload publication. Previously reconciled companies reuse hash-verified payloads and unchanged transformation identities; annual screening is recomputed from that payload. Failures remain explicit quarantines. Usable means at least one supported metric and period, not complete statements or financial certification.

The authorized incremental spend cap is $5. The conservative reservation includes a whole paid Class A and Class B operation unit plus first-month retained data; company materialization stops at 7GB, reserving headroom for catalog publication, bounded update composition and Basin experiments. It excludes existing subscription base charges and future months of retention. Local free-space and elapsed-time guards stop safely. Original archives, source releases and production CURRENT pointers are never rewritten.

A stable materialization-run identity owns company objects and checkpoints. Each catalog publication uses a separate identity derived from its exact checkpoint set. A later resume can therefore publish a new immutable snapshot without conflicting with an earlier partial publication. Hash-pinned, approved inherited source release IDs retain original payload provenance.

The private history API accepts `release_id` only for explicitly approved previous releases. `expected_release_id` still detects unwanted drift. Current and historical responses identify their serving release, materialization release and manifest hash; no caller-supplied object path or arbitrary SQL is accepted.

Metric-specific sorted screening chunks bound memory independently of universe size. Screening retains exact decimals/rationals, complete leaf-source IDs and missingness. See [terminal workflow](terminal-workflow.md), [screening contract](terminal-universe-screen.md), [Basin measurements](basin-optimization.md), and [controlled update limitations](updates-controlled-promotion.md).

Operational credential, TLS and historical-manifest blockers retain the exact actions in [the prior operator guide](../sec-engine-scale/operations.md); this sprint does not retry unauthorized provisioning or expand licensed access.
