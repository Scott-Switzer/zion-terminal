# Controlled SEC update promotion

`sec_incremental_update.py` retains immutable unapproved candidate snapshots. A candidate company object made with an older registry is never copied into a serving catalog. `sec_release_promotion.py` validates candidate identity/object hashes, then rebuilds its raw public SEC snapshot with the exact current implementation and base registry. It verifies registry dependency hashes, compares old/new source IDs and exact same-context values, records removed facts explicitly, runs reported balance-total checks, and requires bounded nonempty financial coverage. A missing or false gate denies composition. Accounting `UNAVAILABLE` remains an explicit limitation; it is not represented as certification.

```sh
python3 scripts/sec_release_promotion.py \
  --base-dir /absolute/path/to/full-current-registry-materialization \
  --candidate-dir /Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale/incremental-live-candidate \
  --output-dir /absolute/path/to/separate-controlled-release
```

The output is a normal materializer-compatible release: `engine-manifest.json`, per-company history and annual/quarterly/TTM expected query files, registry and member index. The parent release and candidate hashes determine its immutable identity. Every inherited company is rewritten sequentially with the new release identity; the original base bytes are retained. Raw facts removed by a new snapshot are never unioned back into it. `promotion.json` records revision/deletion reconciliation, accounting checks, registry migration and a parent rollback identity. No provider requests, archive unzip, configuration edits or CURRENT writes occur.

Publish this directory with the regular create-only catalog publisher, including companies, catalog shards, directory, registry, implementation and screener index. Read-back hash verification and the independent release verifier are required before configuring the authorized staging endpoint to the new immutable manifest reference. An engine manifest or `promotion.json` alone is not a published catalog. The production pointer remains protected.

Run identical historical/as-of queries against the old and new explicit manifest references. Compare each release with its own generated expected files; verify the old reference still reproduces its prior values and the updated Microsoft reference uses the replacement snapshot. Latest retrieval does not imply any facts changed: the reconciliation can correctly be empty. Rollback restores the saved old manifest key/hash in staging configuration; it neither deletes nor mutates either immutable release. No helper in this module performs deployment or pointer mutation.

Validation: unit tests cover incomplete/failed gate denial, explicit revision/deletion reconciliation, current-registry rebuilding, original-release byte preservation and corrupt candidate denial. Additional limitations: anomaly checks bound payload/row size and reject empty coverage; they do not certify every accounting relationship or detect every economically implausible source value. The caller must independently validate published queries before staging activation.

For a full archive streaming pipeline, avoid creating a second inherited local release. Call `validate_candidate_inputs(base_dir, candidate_dir)` once to verify the candidate/base registry. For a changed CIK, call `candidate_overlay(candidate_dir, cik, prior_data, new_release_id, current_registry_sha256)` to obtain `(rebuilt_data, reconciliation_report)` without file writes. Feed that data through the normal publication path and retain the report with the release. All unchanged issuers continue through the streaming materializer. The standalone composition command is suitable only when there is disk space for a complete separate local release.

## Per-company financial updates

The cloud composer now supports newer financial histories for individually updated companies. Their hash-pinned catalog entries carry `maximum_as_of` and `date_to`; the root and all screen indexes retain the parent archive cutoff. Other companies keep their prior ceilings. `update_refresh.per_company_cutoffs` states the selective freshness and the screen limitation explicitly.

Historical parity compares financial output fields and coverage-gap semantics across annual, quarterly and TTM histories at the parent cutoff. Raw snapshot hashes and raw citation IDs may legitimately change when SEC revises frame/fiscal metadata. Every signed source term is therefore resolved to its original financial context (concept, unit, currency, exact value, period, filed date, availability, accession and form), and its coefficient is compared. Changed component values cannot pass merely because their net derived value is unchanged. Added/removed raw IDs remain visible in reconciliation and the immutable old release keeps its original citations. Older release bytes are never overwritten.

Changed financial updates require at least one reported accounting identity PASS, no identity FAIL, no loss of supported metrics, bounded payload/row counts and independent raw reconciliation of all three histories at the new company cutoff. A candidate that fails any gate remains unpromoted. The composer does not claim that every company or the historical screener is current to the candidate retrieval date.

New composed roots explicitly select `balance_quality_version=sec-balance-same-vintage-v1` and pin the helper implementation. Accounting totals from mismatched filing vintages are unavailable comparisons, with their source IDs retained; unequal totals from the same filing remain failures. Older roots retain their original balance-check behavior because they do not carry this policy flag. The normalizer and original registry remain frozen.
