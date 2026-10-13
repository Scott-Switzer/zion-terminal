# Private financial dataset connection

`zion-datasets` connects the four existing financial R2 stores without copying
records, changing publication pointers, or acquiring provider data. All stored
objects in these stores are discoverable with pagination and readable in bounded
byte ranges. New datasets appear automatically; a code release is not required.

| Store ID | Existing R2 bucket | Contents |
| --- | --- | --- |
| financial | financial-system-datasets | Market, SEC, normalized provider, raw, research, and published serving datasets |
| research | financial-data-wrds-private | WRDS tables, private research, inventories, public SEC archives |
| filings | ppe-sec-intelligence-prod | SEC filing intelligence corpus |
| lakehouse | financial-data-wrds-lakehouse | Iceberg objects and metadata |

This is a **storage connection**, not a universal financial schema or SQL engine.
Raw/provider/canonical/serving layers retain their original names and bytes.
Neither an R2 upload timestamp nor an ETag proves financial availability, freshness,
point-in-time validity, or accuracy. Synthetic fixtures and quarantine material
remain identifiable by their original paths; discovering them does not approve
use in calculations. Private provider restrictions remain in force.

## Access boundary

The Worker has no public route, workers.dev endpoint, or preview URL. Its default
entrypoint must only be bound to trusted private research consumers. It exposes
read-only GET/HEAD operations and no provider execution. Native queries persist
only derived query-cache envelopes in the financial store; source objects and
publication pointers are never rewritten. See `native-collection-queries.md`.

The named `Catalog` entrypoint has **one** route, `GET /v1/datasets/status`. It
returns fixed source labels and storage connectivity only. It never returns object
paths, table names, contents, provider credentials, or bucket names. This is the
only entrypoint attached to the public DCF app (`ZION_DATASET_CATALOG`). No public
user, including signed-in users, receives the private default entrypoint.

The existing compact `zion-valuation` service remains the approved SEC field path
for ticker autofill. Public DCF access to broader licensed source records is not
enabled by this storage connection.

## Private API

- `GET /v1/datasets`: configured stores and read budgets; no storage probe.
- `GET /healthz`: probe each store; report unavailable/empty separately.
- `GET /v1/datasets/{store}/objects?prefix=raw/wrds/&limit=200`: one directory
  page. `recursive=true` lists objects recursively. Follow `cursor` while
  `truncated` is true, even if the current objects list is empty. Cursors are
  scoped to store, prefix and recursion setting, not authorization tokens.
- `HEAD /v1/datasets/{store}/object?path=...`: size and quoted ETag.
- `GET /v1/datasets/{store}/object?path=...`: send exact `If-Match` from HEAD
  (or quote the list response ETag). Whole reads are capped at 1 MiB.
- Add `offset=N&length=N` for a range of at most 1 MiB. The response streams the
  original bytes and includes `Content-Range`. Large ZIP/Parquet files can be
  read selectively without loading the entire object into Worker memory.

A changed ETag fails with 412, missing precondition with 428, missing object with
404, invalid range with 416, unbounded large-object request with 413, and storage
failure with a sanitized 503. Responses are private/no-store. ETag conditional
reads prevent mixed object versions; callers using a release manifest must still
verify its SHA-256 pins and financial semantics themselves.

## Operator use

Start the checked-in local-only bridge using authenticated Wrangler:

```sh
npx wrangler dev --config deploy/datasets/client/wrangler.jsonc
python3 scripts/datasets_client.py list research --prefix raw/wrds/ --limit 200
python3 scripts/datasets_client.py head research --path PATH_FROM_LIST
python3 scripts/datasets_client.py read research --path PATH_FROM_LIST --offset 0 --length 4096 --output /tmp/selected-range.bin
```

The bridge binds 127.0.0.1 and rejects non-loopback hostnames and browser Origin
headers. Do not deploy this operator proxy. Wrangler supplies the authenticated
remote binding; no new provider/API credentials are needed. The reader automatically
uses HEAD/If-Match and refuses a non-loopback URL. Stop Wrangler when finished.

The connection includes the financial lakehouse's stored files, but does not
pretend direct object reads are transactionally consistent Iceberg SQL queries.
Unrelated music/festival stores, machine backups, and test buckets are excluded.
D1 user valuations and accounts are not exposed by this service.
