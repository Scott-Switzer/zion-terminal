# Native collection catalog and cloud table queries

The private API now provides a searchable, immutable catalog and native Parquet
queries in the existing Cloudflare dataset service. No provider requests, WRDS
collection, Cloud Run provisioning, or bulk local data processing is involved.

## Catalog

- `GET /private/v1/datasets/catalog?search=crsp&family=crsp&offset=0&limit=50`
- `GET /private/v1/datasets/catalog/{id}`

The published inventory registers **805 recorded releases, 803 named datasets,
and 41,485 object references**. One legacy entry has an unknown declared dataset
name; multiple releases are retained. It preserves all source-declared columns,
object references, manifest hashes and coverage limitations. Registration is not
verification of every data object. The October 5 source inventory is partial and
records 350 errors; missing manifests are not promoted to complete data.

This catalog initially registers the existing WRDS inventory. The financial,
filing and lakehouse stores remain dynamically discoverable using `/objects`.
The catalog also references the existing hash-pinned cross-domain coverage report.
Additional native datasets can be queried by discovered Parquet path without
waiting for a catalog registration.

## Schema and rows

- `GET /private/v1/datasets/{store}/schema?path=PATH_FROM_CATALOG`
- `GET /private/v1/datasets/{store}/rows?path=PATH&columns=permno,date,ret&offset=0&limit=100`
- Repeat the row request with its `cursor` and the same store, path and column
  projection. Do not combine a cursor with an offset. A changed source version
  or projection fails instead of mixing results. `If-Match` optionally pins the
  first request to a previously observed exact quoted ETag.

Schemas decode actual Parquet footers; row counts are footer-declared, not a full
row census. Queries return source field names, physical row positions, native
schema and source version. There is no company, financial metric or history cap.
Each request returns 1–1,000 rows and up to 256 projected columns; follow the
cursor for the entire object. Select other registered parts to traverse a table.

Compact results persist in the existing financial R2 store with SHA-256 checks
and source-version/query keys. `cache` distinguishes a fresh cloud decode from a
persisted result. Original source objects and publication pointers are preserved.

Exact int64 and decimal values are strings. Decimal scale is retained. Native
source timestamps use an epoch string and their millisecond/microsecond/nanosecond
unit; timezone interpretation is in the returned source schema. Dates are ISO
date strings. Null remains null. NaN and infinities use explicit `non_finite`
objects. Binary fields are base64 with an encoding label. JSON-encoded fields
retain their JSON text rather than losing embedded numeric precision.

Supported codecs: uncompressed, Snappy, Gzip and Zstd. Nested/repeated schemas,
other codecs and oversized column groups require a separate query adapter.
Limits are 8 MiB of compressed/source reads, 16 MiB of declared selected decoded
column chunks, and 1 MiB response per request. Exceeding a budget returns 413;
it never silently drops rows or returns missing data as zero. Smaller projection
or a larger cloud query runtime is necessary for oversized groups. Very wide
or large footers can also exceed the read budget.

## Terminal client

With existing private credentials loaded into the environment:

```sh
python3 scripts/datasets_client.py tables --hosted --search crsp --limit 50
python3 scripts/datasets_client.py schema research --hosted --path PATH_FROM_CATALOG
python3 scripts/datasets_client.py rows research --hosted --path PATH_FROM_CATALOG --columns permno,date,ret --limit 100 --output /tmp/native-rows.json
```

Row output requires an explicit file and is saved with mode 0600. Tokens are
never command arguments; redirects are rejected. Metadata commands may display
private dataset and field names to the authenticated operator.

## Semantic boundary

This is a native-source query layer, not canonical financial normalization,
cross-file SQL, a transactional Iceberg snapshot, adjusted-price reconciliation,
or point-in-time certification. It does not infer that source ETags are financial
availability dates. Public valuation consumers keep their restricted catalog
entrypoint and cannot reach these routes. Licensed records remain private.
