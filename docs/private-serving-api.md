# Private existing-data serving API

Implementation branch only: not deployed or live-accepted. This first slice reads
existing US-company financial artifacts; it does not collect WRDS or call providers.
The frozen public tool registry and contract fingerprint are unchanged.

Both routes require `Authorization: Bearer <token>`, checked before storage access
and body processing. Runtime secret `ZION_PRIVATE_API_TOKEN` must be configured by
the operator; it is never a tracked variable. Missing runtime configuration returns
503, missing/wrong authentication returns 401. All private responses are no-store.

- `GET /private/v1/capabilities`: implemented operations and pending domains.
- `POST /private/v1/query`: JSON object, at most 32 KiB including streamed bodies.

Example request bodies (send credentials through your client's secret handling):

```json
{"operation":"identity","symbol":"MSFT"}
```
```json
{"operation":"financials","symbol":"MSFT","metrics":["revenue"],"period":"annual","lookback":6,"as_of":"2026-10-13T00:00:00Z"}
```
```json
{"operation":"revenue_growth","symbol":"MSFT","years":5}
```

Optional `expected_release_id` must equal the release pinned once for this request,
otherwise 409. No arbitrary historical release or storage keys are accepted.
`as_of` requires an explicit UTC offset or Z; date-only values are rejected.
Financial requests accept 1–20 distinct metrics, annual/quarterly period and 1–40
observations per metric. Growth accepts 1–10 years and defaults to five.

Responses include `schema_version=zion-private-query-v1`, operation, entity, data,
coverage/missing reasons, evidence identifiers and verified release identity/digest.
Observations retain exact decimal strings, units, fiscal bounds, availability,
source/revision references and separate ingestion time. Internal R2 paths and raw
storage errors are omitted. Integrity is not independent financial validation;
observations are labelled SOURCE_UNVALIDATED until separately reconciled.

Growth returns consecutive annual YoY percentages and cumulative growth, using
Decimal. It requires six comparable years for five annual rates, with identical
issuer, units, accounting basis and release, full fiscal years and positive revenue.
52/53-week years are supported; missing years, ambiguous revisions, missing basis,
nonpositive revenue and changed units fail explicitly. CAGR is not emitted.
The current stored release may lack accounting-basis metadata: financials remain
available but growth then returns COMPARABILITY_NOT_ESTABLISHED. This is an explicit
coverage gap, not permission to infer a basis or manufacture a calculation.

Identity and financial temporal filters use the existing release and temporal
implementation. Historical identity without a supported dated resolver fails
closed. Missing metrics return partial coverage; unavailable/corrupt objects are
sanitized errors. No blanket full-statement, freshness or real-time claim is made.

Estimates, prices, macro, historical research, free-provider gap filling and derived
packet publication are subsequent slices. Licensed private validation does not
turn source records into publicly distributable data.

## Acceptance client

Set `ZION_PRIVATE_BASE_URL` to the credential-free HTTPS origin and supply
`ZION_PRIVATE_API_TOKEN` through your existing secret mechanism. Then run:

```sh
python scripts/private_serving_acceptance.py --output /tmp/zion-private-acceptance.json
```

The client refuses redirects, probes absent/wrong authentication, pins each
company's release across identity/financials/growth calls and writes only
sanitized status, coverage reasons and sequential request timings. Exit 0 means
PASS; 1 means coverage PARTIAL; 2 means FAIL/configuration error. It does not
print financial records, authorization headers or exception messages.
Do not run it against an unapproved deployment or place a token in shell history.
