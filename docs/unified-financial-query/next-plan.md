# Demonstrated gaps and next implementation sequence

0. Diagnose custom-hostname TLS negotiation separately; verify it externally and rerun authenticated checks there before declaring that hostname reachable. Existing Worker-origin checks pass.
1. Keep licensed SQL disabled until a narrowly scoped SQL/catalog-read credential and explicit source migration/serving entitlements are verified. Never send licensed rows to an LLM.
2. Resolve the one legacy path reference with its producer contract. Investigate the 340 release directories with objects but no expected manifest; do not fabricate manifests or assume they are complete. Add a bounded metadata reader for the ten 1.3–4.2 MB manifests, retaining hashes and reader limits.
3. Run a resumable, rate-limited footer/codec/schema census against existing objects only. Inventory counts are not evidence of decodability. Reconcile orphan objects, catalog tables and actual queryable snapshots. Do not bulk rewrite archives.
4. Expand public SEC mappings only with source-context, units, fiscal-period, availability and restatement validation. Establish source-backed historical security links before ticker-based historical queries. Preserve unmapped raw records.
5. Benchmark a larger authorized public-only Iceberg partition pilot; establish account cost telemetry and actual cancellation behavior before increasing budgets. No automatic DuckDB fallback or production pointer promotion.
6. Add source-specific entitlement policies and complete contract validation before any licensed multi-table pilot. Each new source/table and any canonical publication requires its own acceptance evidence and promotion gate.
