# Full private financial collection access

The existing four financial stores are now available through the authenticated
private API, using the same private token as `/private/v1/query`. This path has
no company, metric, fiscal-period, source-format, or total-history restriction.
Pagination and byte ranges bound individual requests; they do not bound the
collection. Newly uploaded objects become discoverable without a code release.

- `GET /private/v1/datasets` — stores and access budgets.
- `GET /private/v1/datasets/{store}/objects?prefix=&recursive=true&limit=200`
  — browse native datasets, manifests, schemas, partitions and archives. Follow
  `cursor` until `truncated` is false, including pages with no objects.
- `HEAD /private/v1/datasets/{store}/object?path=...` — exact size and ETag.
- `GET /private/v1/datasets/{store}/object?path=...&offset=0&length=1048576`
  with the exact `If-Match` ETag — stream source bytes. Continue ranges as needed.
  Changed versions fail rather than mixing object versions.

Stores: `financial` (market/provider/normalized/raw/serving), `research` (private
WRDS tables, research, public SEC archives), `filings` (filing intelligence), and
`lakehouse` (Iceberg data and metadata). Machine backups and unrelated festival
stores are outside the financial collection. Public valuation routes and their
restricted catalog binding do not gain access to this private default service.

Native terminal access requires credentials already present in the environment:

```sh
python3 scripts/datasets_client.py catalog --hosted
python3 scripts/datasets_client.py list research --hosted --prefix raw/wrds/ --recursive --limit 200
python3 scripts/datasets_client.py head research --hosted --path PATH_FROM_LIST
python3 scripts/datasets_client.py read research --hosted --path PATH_FROM_LIST --offset 0 --length 4096 --output /tmp/selected-range.bin
```

Set `ZION_PRIVATE_BASE_URL` to the existing private HTTPS API origin and load
`ZION_PRIVATE_API_TOKEN` through the existing private credential mechanism.
Do not put tokens in arguments or URLs. The client rejects redirects and stores
read results only in an explicitly requested output file, never automatic stdout.

## Existing coverage evidence

The financial store contains the hash-addressed full financial coverage report:
`control/full-financial-v1/report/releases/6bd3ec154854fc027c4f0f96a68c0b43890cf765b51095ce8f472119361fdd20.json`.
Its SHA-256 is the final filename component. It references 19 hashed artifacts
covering prices, fundamentals, filings, identity, corporate actions, ownership,
insiders, estimates, options, short interest, macro and news. Read referenced
artifacts through the same API and verify their hashes before using claims.
The private research store also contains the WRDS inventory under
`control/full-financial-v1/wrds/inventory/`; no new WRDS collection runs.
These are existing historical evidence, not a fresh full data-quality census.

## Remaining normalization work

Full storage access does not mean every raw table is normalized or every field
has a financial query adapter. The SEC `sec_financials` operation remains one
explicit 20-metric projection. All original concepts and longer histories remain
available through the underlying source objects. Parquet/Iceberg datasets need
compatible table-query consumers; direct ranges are not an Iceberg SQL snapshot.

Do not collapse source-specific identifiers, units, revisions, price adjustments
or availability semantics. Licensed/private sources remain private, and their
availability does not authorize public redistribution. The dataset/schema catalog and cloud native Parquet query milestone is now
implemented; see `native-collection-queries.md`. Cross-file query execution and
validated cross-source financial mappings remain separate work. The API states
which layer each result represents.
