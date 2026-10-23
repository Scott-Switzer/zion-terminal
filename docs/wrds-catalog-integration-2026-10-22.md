# Completed WRDS exports in the existing dataset catalog

The remote collector writes lossless PostgreSQL native text into Parquet strings.
Those archives are not typed financial facts. `extend_collection_catalog.py`
reuses the existing catalog publisher, dataset service, and native reader. It
reads the previous catalog and completion markers under
`control/wrds/preservation-staged/`, verifies manifest hashes and part/row
accounting, and preserves native column types alongside the string storage type.
It never reads Parquet records, acquires provider data, or advances CURRENT.

The immutable candidate is recorded in `wrds-catalog-candidate-2026-10-22.json`:
143 new completed releases; 948 total releases, 946 distinct datasets and 46,499
object references. All candidate objects were created conditionally and read
back. Existing releases remain referenced. Partial exports lack completion
markers and are excluded. One older stocknames receipt uses a different schema
and remains an explicit reconciliation gap. Counts are source-declared, not an
independent certification of row contents or full WRDS coverage.

The source's bundle inventory now includes the catalog release and manifest
hash. The dataset config and both source release configs carry the same pin.
The shared API can therefore reject a catalog/source rollout mismatch. This is
necessary before the MCP can safely answer metadata-only `WRDS coverage` and
`WRDS datasets comp` questions. Licensed rows remain behind existing owner-only
dataset access; no new reader tool or public route is added.

Reproduce with the existing private R2 profile (credentials are never printed):

```sh
python scripts/extend_collection_catalog.py \
  --base-key gold/collection-catalog/releases/ae29d97f14d6052fb4da7fd963a32e4816437e58c20060ce6416ecb769a42c1a/manifest.json \
  --base-sha256 5dc9df9730425b93525b33a738275cfcafbe91f3e1364c7f5622f3146bdaf73c \
  --output-metadata /tmp/wrds-catalog-candidate.json
```

`--publish` writes immutable metadata candidates only. It does not deploy or
promote their config pins. Repeat scans of unchanged completion markers have the
same content identity; acquisition time does not enter that identity.

Deploy dataset service, thin source, shared query API, then MCP. Verify the
catalog release against the source bundle and a prior-session query through MCP.
Preserve the old Worker versions for rollback. The publication candidate's R2
readback is live evidence; local tests and deployment dry runs are not evidence
that the new consumer routes are deployed.

Next normalization must be dataset-specific: source units, date semantics,
identifier validity intervals, revisions, availability timestamps, and usage
policy. Unknown values stay unknown. No financial statement quarantine is lifted,
no providers are spliced, and no raw decimal string is converted to a float here.
