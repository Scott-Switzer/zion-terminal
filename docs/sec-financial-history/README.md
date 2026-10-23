# Verified SEC financial-history service

The private service now supports five stored public SEC companies: Apple (0000320193), Costco (0000909832), NVIDIA (0001045810), Meta (0001326801), and Alphabet (0001652044). This is bounded source and arithmetic verification, not full financial certification. See [acceptance gates](acceptance-gates.json), [company coverage](company-coverage.json), and [benchmarks](benchmarks.json).

## API

Authenticated GET on `https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev/private/v1/financial-history` using the existing private bearer credential. Example query (never embed the credential in a URL):

```text
?cik=0000320193&metrics=revenue,operating_income,net_income,free_cash_flow&frequency=annual&date_from=2015-01-01&date_to=2026-09-24
```

Parameters: ten-digit supported `cik`; comma-separated `metrics`; `frequency=annual|quarterly|ttm`; inclusive period-end `date_from`/`date_to`; optional RFC3339 `as_of`; optional `expected_release_id`; optional `recipient=human_research`. Defaults use the pinned manifest. Maximum range is 12 years within 2015-01-01 through 2026-09-24. The conservatively verified availability cutoff is 2026-09-24T00:00:00Z because the stored archive has a date-only snapshot. Later cutoffs are rejected. There are no fresh provider requests.

Metrics: revenue, operating_income, net_income, operating_cash_flow, capital_expenditures, cash, total_debt, diluted_shares, and derived free_cash_flow. USD and shares are preserved without conversion. Values and source `val` fields are exact decimal strings. Original SEC numeric records remain in the hash-pinned archive. Results distinguish normalized and derived observations, include complete leaf source records and linear calculation terms, source concept definitions, release/member hashes, filing accession, availability, and explicit coverage gaps. No missing value is treated as zero.

Only supported public SEC data are permitted. Unknown datasets/SQL/recipients/companies are denied; writes return 405, authentication failures 401, invalid input 400, source/release conflicts 409, and oversized responses 413. Bounds: 20-second caller deadline, 16 MiB company source, 5,000 source observations, 1,000 output observations, and 2 MiB response. Caller timeout does not establish cancellation of underlying operations. No silent truncation.

## Accounting and availability

Source revisions are retained. Eligibility is applied before latest-revision selection; same-availability conflicting values produce a gap. Date-only filing availability begins at the next New York midnight, conservatively after the filing date. Point-in-time results mean availability in this stored SEC snapshot, not proof of when an external vendor first disseminated the fact.

Annual contexts must be 330–380 days with annual forms; quarters 75–105 days except Costco's documented 75–120-day range for 16/17-week Q4. Cash-flow quarters may be derived by subtracting aligned cumulative observations of the same concept and fiscal start. Weighted diluted shares are never differenced or summed. TTM requires four contiguous validated additive quarters of the same concept, spanning 330–380 days. FCF is aligned CFO minus nonnegative PP&E cash payments. Definitions and alias precedence are versioned in the metric registry; conflicting aliases remain gaps. NVIDIA's broader PP&E/intangible purchase concept is not silently equated with this capex definition.

Costco revenue uses `Revenues` to include membership fees; contract revenue/net sales alone is insufficient. Debt means borrowings plus capital/finance leases, excluding operating leases. `DebtCurrent` includes current leases while `LongTermDebtNoncurrent` excludes leases: the additional noncurrent lease component is mandatory. Apple and Costco debt are quarantined pending filing-level nonoverlap confirmation; Meta lacks required current-debt evidence; NVIDIA lacks matching lease evidence; only three Alphabet annual debt periods currently qualify. Missing components never imply zero. Per-company frequencies, missingness, rejection counts, and required components are in company-coverage.json. Gap counts include mapping/context exclusions, not a census of expected fiscal periods.

## Verification and preservation

3,117 returned observations independently reconcile to stored SEC source values and exact Python/DuckDB arithmetic; 6,286 normalized revisions match an isolated two-table Basin join. Sixty live API checks, 79 old-route regression checks, 167 Python tests and 39 Worker tests pass. Fifteen non-debt company/frequency comparisons reproduce across two immutable releases; intentionally changed debt definitions are excluded explicitly. These checks do not certify every financial concept or company.

Ten oversized manifests were recovered with an 8 MiB bounded, ETag/length/hash-checked reader and schema/part/row validations. Part presence/size checks use the earlier metadata census; full part bodies were not rehashed. The 340 unsealed directories and one ambiguous legacy relative reference are explicitly quarantined in the audit, without invented manifests or promotion. Producer cause remains unknown. Original archives, immutable releases and all three production CURRENT pointers remain unchanged.

Protected operator evidence is in `/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-financial-history`; sanitized evidence hashes are in acceptance-gates.json. No credentials or licensed row payloads are committed. The complete estate is not yet certified queryable.


### Stored-data-only callers

The engine-backed history and batch routes accept `materialized_only=true`.
They return `202` with `MATERIALIZATION_REQUIRED` if a company is not already
materialized, without submitting a queue task. Omit the option to retain the
existing owner acquisition behavior. Other values are rejected. Stored query
calculation/cache writes may still occur; this option prevents new archive
materialization, not ordinary result caching. A batch stops with the same gap
response if any member requires materialization. Explicit jobs remain on the
existing jobs endpoint.
