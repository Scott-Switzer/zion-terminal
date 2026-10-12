# Private financial dataset registry (M1)

`src/zion_terminal/datasets/` is a read-only client of the private `zion-datasets`
service plus a typed SEC annual fundamentals contract. It answers one question
per issuer: *which of the contract fields are actually present, in the right
units, from an eligible filing, and corroborated by a second source?*

It is discovery plus financial validation, not a warehouse. It never writes,
copies, re-points, publishes or re-keys any dataset, and nothing here is wired
to the public valuation site. It does not scan whole WRDS archives: every list
is bounded by prefix, page count and object budget.

## Storage boundary

The registry reaches only the four buckets already bound to `zion-datasets`
(`deploy/datasets/wrangler.jsonc`): `financial-system-datasets`,
`financial-data-wrds-private`, `ppe-sec-intelligence-prod` and
`financial-data-wrds-lakehouse`. Reads go through the read-only service; the
Python side cannot write. Outputs are split by sensitivity:

| artifact | contents | destination |
|---|---|---|
| `report.json` | full evidence incl. store paths, object versions, ETags, hashes | ignored (`artifacts/registry-m1/`) |
| `report.md` | human-readable full evidence | ignored |
| `inventory.json` | every object read with its quality state | ignored |
| `summary.json` | states, values, concepts and reason codes, no private provenance | safe to commit |
| `summary.md` | the same evaluated observations as markdown, no private provenance | safe to commit |

`artifacts/registry-m1/` is git-ignored because it names private store paths and
object hashes. Only the sanitized summary and markdown may leave the machine.
Storage access is never treated as evidence of availability dates, freshness or
redistribution rights.

## Modules

- `transport.py` — loopback HTTP bridge to `zion-datasets` (default
  `http://127.0.0.1:8791`), cursor paging, ETag-pinned bounded range reads,
  byte budgets, zip central-directory + single-member reads without buffering
  the archive. `FixtureBridge` replays a synthetic tree for offline tests.
- `registry.py` — declared source families, bounded discovery, release pointer
  and manifest resolution with hash pinning, approved valuation-packet index and
  packet reads, SEC companyfacts source resolution (corpus first, bulk-archive
  member as fallback), and the read inventory with per-object quality states.
- `sec_fundamentals.py` — the typed contract, XBRL fact parsing, annual period
  selection, point-in-time eligibility, field evaluation, both derived-debt
  reconciliations, and cross-source validation.
- `report.py` — company-by-field reconciliation, serving-coverage mapping,
  content-addressed report digest, sanitized exports.
- `cli.py` / `__main__.py` — `python -m zion_terminal.datasets`.

### Source families

`serving_release_candidate`, `serving_release_promoted`, `sec_facts_release`,
`sec_companyfacts_corpus`, `sec_bulk_archive`, `company_dataset`,
`native_sec_layer`, `valuation_packets`, `wrds_inventory`. Declaring a family is
not evidence that a dataset exists in it; resolution must succeed and be
recorded.

### CLI

```bash
PYTHONPATH=src python -m zion_terminal.datasets catalog
PYTHONPATH=src python -m zion_terminal.datasets discover --family sec_bulk_archive --limit 25
PYTHONPATH=src python -m zion_terminal.datasets release --family serving_release_promoted --sample 3
PYTHONPATH=src python -m zion_terminal.datasets report --tickers AAPL,MSFT,NVDA --as-of 2026-10-11
PYTHONPATH=src python -m zion_terminal.datasets report --tickers TSTA,TSTB --fixture tests/fixtures/datasets/tree
```

## The SEC annual fundamentals contract

Contract schema: `zion-sec-fundamentals-contract-v1`. Three most recent eligible
annual periods per issuer (330–380 day durations), reported concepts only; no
value is ever manufactured.

| field | kind | unit | reported concepts (priority order) |
|---|---|---|---|
| `revenue` | duration | USD | `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `RevenueFromContractWithCustomerIncludingAssessedTax`, `SalesRevenueNet` |
| `operating_income_ebit` | duration | USD | `OperatingIncomeLoss` |
| `net_income_common` | duration | USD | `NetIncomeLossAvailableToCommonStockholdersBasic`, `NetIncomeLoss` |
| `depreciation_amortization` | duration | USD | `DepreciationDepletionAndAmortization`, `DepreciationAmortizationAndAccretionNet`, `DepreciationAndAmortization` |
| `capital_expenditure` | duration | USD | `PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsToAcquireProductiveAssets` |
| `diluted_weighted_average_shares` | duration | shares | `WeightedAverageNumberOfDilutedSharesOutstanding` |
| `cash_and_equivalents` | instant | USD | `CashAndCashEquivalentsAtCarryingValue` |
| `debt_current` | instant | USD | `LongTermDebtCurrent`, `DebtCurrent` |
| `debt_noncurrent` | instant | USD | `LongTermDebtNoncurrent`, `LongTermDebtAndCapitalLeaseObligations` |
| `debt_short_term_borrowings` | instant | USD | `CommercialPaper`, `ShortTermBorrowings` |
| `debt_total_reported` | instant | USD | `LongTermDebt` (cross-check only) |

Two derived fields are always labelled as derived:

- `interest_bearing_debt_total` = `debt_noncurrent` + `debt_current`, reconciled
  against `debt_total_reported` with rounding slack (0.5% / $1M).
- `debt_short_term_total` = `debt_current` + `debt_short_term_borrowings`.

Explicit distinctions are preserved, never flattened:

- **Net income vs common income.** When only consolidated `NetIncomeLoss` exists,
  the value is labelled `NET_INCOME_TO_COMMON_UNADJUSTED` (reason code
  `NET_INCOME_TO_COMMON_UNADJUSTED`) and is *basis-changing*: it can never be
  promoted to `SOURCE_VALIDATED`, even if a second source agrees with the number.
- **Total debt vs interest-bearing debt.** `debt_total_reported` is an
  issuer-reported long-term debt subtotal — not total liabilities, not operating
  leases — and is used only to reconcile the split.
- **Diluted weighted-average shares vs shares outstanding.** An instant common
  count is a different measure and does not satisfy the field.
- **Capex magnitude.** Reported as a positive cash-outflow magnitude, never
  negated.

Each observation keeps issuer CIK, source concept/tag, fiscal period and fiscal
year, amount, currency and unit, filing date, availability instant, accession,
release identifier, source-object integrity reference, point-in-time
eligibility, and validation state with reason codes. Cursor-bound listing,
ETag-pinned reading and release manifests give each value an object version.

## Quality states

`DISCLOSED_STATES` for a field (`sec_fundamentals.py`):

| state | meaning |
|---|---|
| `PASS` | reported, correct unit, period aligned, corroborated |
| `PASS_DERIVED` | derived from validated parts; the derivation is named |
| `SOURCE_VALIDATED` | single authoritative source, schema-checked, no second source |
| `SOURCE_UNVALIDATED` | present but not independently corroborated |
| `SOURCE_PARTIALLY_VALIDATED` | company-level roll-up of the two above |
| `CONFLICT` | sources disagree beyond tolerance; values are kept, never averaged |
| `MISSING_OBSERVATION` | no eligible observation; alternative issuer tags are named |
| `UNIT_MISMATCH` | observed unit differs from the contract unit |
| `PERIOD_MISMATCH` | observation is not on the requested fiscal period |
| `NOT_PIT_ELIGIBLE` | filing/availability is later than the as-of instant |
| `NOT_CONFORMING` | observation does not satisfy the contract definition |
| `BLOCKED` | the source could not be read; the reason is recorded |

Per-object inventory quality (`registry.py`): `DISCOVERED` (listed, not read),
`HASH_VERIFIED`, `SCHEMA_VALIDATED`, `SOURCE_UNVALIDATED`, `PARTIAL`, `ABSENT`,
`BLOCKED`.

`SOURCE_UNVALIDATED` is the honest default. Aggregate manifest row counts are
never evidence that an individual observation is correct, and a `200` read is
never evidence of financial correctness.

## Point-in-time

A fact is eligible only when its availability instant is `<= as_of`. A filing
dated after `as_of` cannot contribute. Date-only filing dates are normalized
under the documented `DATE_ONLY_NEXT_DAY_ET_V1` policy
(`docs/FINANCIAL_TEMPORAL_CORE_V1.md`). Stored object timestamps and ETags are
storage metadata, not observation dates, and are never used to establish
availability.

## Limitations

- The published serving layer supports only `revenue`, `operating_income` and
  `cash` on the contract basis: `net_income` and shares outstanding differ in
  basis, and D&A, capex and the debt split are absent from its metric catalog.
  Cross-validation therefore reaches only a subset of fields.
- Where a field has exactly one eligible source, its state stays
  `SOURCE_UNVALIDATED`; that is a coverage limit, not a defect in the value.
- The `serving_release_candidate` manifest names artifacts that are not stored
  in R2; the registry records the gap rather than treating the manifest as
  complete.
- Coverage is SEC annual fundamentals only. No Compustat/CRSP, no quarterly
  contract, no new public dashboard.
- Redistribution rights are not inferred from storage access.

## Testing

`tests/unit/test_dataset_{transport,registry,report}.py` and
`tests/unit/test_sec_fundamentals_contract.py` run entirely on the synthetic
tree in `tests/fixtures/datasets/tree` (regenerate with
`python tests/fixtures/datasets/build_fixtures.py`; the zip uses a pinned
timestamp so the tree is byte-stable). They cover cursor paging, ETag changes,
manifest hash/identity validation, missing observations, wrong units, fiscal
period mismatch, source identity mismatch, point-in-time blocking, source-to-
contract mapping, quality-state transitions, and public/private leakage guards
on both sanitized exports.
