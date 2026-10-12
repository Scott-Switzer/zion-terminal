# Financial dataset registry report (zion-financial-registry-report-v1)

- Generated: `2026-10-12T17:41:09+00:00`
- Mode: `live`
- As-of instant: `2026-10-11`
- Fundamentals contract: `zion-sec-fundamentals-contract-v1`
- Report digest: `a107fecd673573038f46544aceebdbf9b9f126a05b4dd66a11b099f59dcaf15e`

## Sources

- `sec_bulk_archive`: resolved
- `serving_release_candidate`: resolved
  - MANIFEST_SHA256_MATCH
  - MANIFEST_IDENTITY_OK
  - MANIFEST_ARTIFACTS_NOT_STORED
- `serving_release_promoted`: resolved
  - MANIFEST_SHA256_MATCH
  - MANIFEST_IDENTITY_OK
- `valuation_packets`: resolved
  - APPROVED_PACKET_INDEX_READ
- `sec_facts_release`: resolved
  - MANIFEST_LISTED_NOT_POINTERED

## Serving coverage of the contract

| contract field | serving metric | state | notes |
| --- | --- | --- | --- |
| `revenue` | revenue | SUPPORTED | Consolidated revenue. |
| `operating_income_ebit` | operating_income | SUPPORTED | Consolidated operating income. |
| `net_income_common` | net_income | BASIS_DIFFERS | Serving metric is consolidated net income, not income attributable to common shareholders. |
| `depreciation_amortization` | — | ABSENT_FROM_SERVING_CATALOG | No depreciation/amortization metric is published in the serving metric catalog. |
| `capital_expenditure` | — | ABSENT_FROM_SERVING_CATALOG | No capital expenditure metric is published in the serving metric catalog. |
| `diluted_weighted_average_shares` | shares_outstanding | BASIS_DIFFERS | Serving metric is an instant common shares outstanding count, not diluted weighted-average shares. |
| `cash_and_equivalents` | cash | SUPPORTED | Cash and cash equivalents at period end. |
| `debt_current` | — | ABSENT_FROM_SERVING_CATALOG | Serving catalog publishes no current/noncurrent debt split. |
| `debt_noncurrent` | — | ABSENT_FROM_SERVING_CATALOG | Serving catalog publishes no current/noncurrent debt split. |
| `debt_short_term_borrowings` | — | ABSENT_FROM_SERVING_CATALOG | Serving catalog publishes no short-term borrowings metric. |
| `debt_total_reported` | total_debt | PARTIAL | Serving metric is a selected reported long-term debt obligation, not total interest-bearing debt and not total liabilities. |
| `interest_bearing_debt_total` | — | NOT_DERIVABLE | Requires a current/noncurrent split that the serving layer does not publish. |
| `debt_short_term_total` | — | NOT_DERIVABLE | Requires a short-term borrowings split that the serving layer does not publish. |

## Companies

### AAPL (CIK 0000320193)

- Overall: `SOURCE_PARTIALLY_VALIDATED`
- State counts: {"PASS_DERIVED": 6, "SOURCE_UNVALIDATED": 16, "SOURCE_VALIDATED": 17}
- Source identity `published_source_evidence_sha256`: MATCH
- Source identity `approved_packet_sec_source_cash`: DIFFERENT_SOURCE_OBJECT

**Fiscal period ending 2025-09-27**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 416161000000 | USD | RevenueFromContractWithCustomerExcludingAssessedTax |
| `operating_income_ebit` | SOURCE_VALIDATED | 133050000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 112010000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | SOURCE_VALIDATED | 11698000000 | USD | DepreciationDepletionAndAmortization |
| `capital_expenditure` | SOURCE_VALIDATED | 12715000000 | USD | PaymentsToAcquirePropertyPlantAndEquipment |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 15004697000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_VALIDATED | 35934000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 12350000000 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | SOURCE_UNVALIDATED | 7979000000 | USD | CommercialPaper |
| `debt_noncurrent` | SOURCE_VALIDATED | 78328000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 90700000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 90678000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | PASS_DERIVED | 20329000000 | USD | LongTermDebtCurrent+CommercialPaper |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `debt_current` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_total_reported` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION

**Fiscal period ending 2024-09-28**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 391035000000 | USD | RevenueFromContractWithCustomerExcludingAssessedTax |
| `operating_income_ebit` | SOURCE_VALIDATED | 123216000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 93736000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | SOURCE_VALIDATED | 11445000000 | USD | DepreciationDepletionAndAmortization |
| `capital_expenditure` | SOURCE_VALIDATED | 9447000000 | USD | PaymentsToAcquirePropertyPlantAndEquipment |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 15408095000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_UNVALIDATED | 29943000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 10912000000 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | SOURCE_UNVALIDATED | 9967000000 | USD | CommercialPaper |
| `debt_noncurrent` | SOURCE_UNVALIDATED | 85750000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 96662000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 96662000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | PASS_DERIVED | 20879000000 | USD | LongTermDebtCurrent+CommercialPaper |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `cash_and_equivalents` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_current` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_noncurrent` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_total_reported` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION

**Fiscal period ending 2023-09-30**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 383285000000 | USD | RevenueFromContractWithCustomerExcludingAssessedTax |
| `operating_income_ebit` | SOURCE_VALIDATED | 114301000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 96995000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | SOURCE_VALIDATED | 11519000000 | USD | DepreciationDepletionAndAmortization |
| `capital_expenditure` | SOURCE_VALIDATED | 10959000000 | USD | PaymentsToAcquirePropertyPlantAndEquipment |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 15812547000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_UNVALIDATED | 29965000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 9822000000 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | SOURCE_UNVALIDATED | 5985000000 | USD | CommercialPaper |
| `debt_noncurrent` | SOURCE_UNVALIDATED | 95281000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 105103000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 105103000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | PASS_DERIVED | 15807000000 | USD | LongTermDebtCurrent+CommercialPaper |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `cash_and_equivalents` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_current` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_noncurrent` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_total_reported` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION

### MSFT (CIK 0000789019)

- Overall: `SOURCE_PARTIALLY_VALIDATED`
- State counts: {"MISSING_OBSERVATION": 5, "PASS_DERIVED": 5, "SOURCE_UNVALIDATED": 15, "SOURCE_VALIDATED": 14}
- Source identity `published_source_evidence_sha256`: MATCH
- Source identity `approved_packet_sec_source_cash`: DIFFERENT_SOURCE_OBJECT

**Fiscal period ending 2026-06-30**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 331839000000 | USD | RevenueFromContractWithCustomerExcludingAssessedTax |
| `operating_income_ebit` | SOURCE_VALIDATED | 155237000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 133749000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | MISSING_OBSERVATION | — | USD | — |
| `capital_expenditure` | SOURCE_VALIDATED | 115948000000 | USD | PaymentsToAcquirePropertyPlantAndEquipment |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 7453000000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_VALIDATED | 20935000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 9227000000 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | MISSING_OBSERVATION | — | USD | — |
| `debt_noncurrent` | SOURCE_VALIDATED | 31067000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 40294000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 40294000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | MISSING_OBSERVATION | — | USD | — |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `depreciation_amortization` MISSING_OBSERVATION: NO_SOURCE_ROW_FOR_PERIOD, ALTERNATIVE_TAG_NOT_CONTRACT_EQUIVALENT
  - issuer tag `AmortizationOfIntangibleAssets` = 4700000000 USD (filed 2026-07-29); not contract equivalent
  - issuer tag `Depreciation` = 34300000000 USD (filed 2026-07-29); not contract equivalent
  - issuer tag `FinanceLeaseRightOfUseAssetAmortization` = 5403000000 USD (filed 2026-07-29); not contract equivalent
- `debt_current` SOURCE_UNVALIDATED: SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` MISSING_OBSERVATION: NO_SOURCE_ROW_FOR_PERIOD
- `debt_total_reported` SOURCE_UNVALIDATED: SINGLE_SOURCE_OBSERVATION
- `debt_short_term_total` MISSING_OBSERVATION: MISSING_PART:debt_short_term_borrowings

**Fiscal period ending 2025-06-30**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 281724000000 | USD | RevenueFromContractWithCustomerExcludingAssessedTax |
| `operating_income_ebit` | SOURCE_VALIDATED | 128528000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 101832000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | MISSING_OBSERVATION | — | USD | — |
| `capital_expenditure` | SOURCE_VALIDATED | 64551000000 | USD | PaymentsToAcquirePropertyPlantAndEquipment |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 7465000000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_UNVALIDATED | 30242000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 2999000000 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | SOURCE_UNVALIDATED | 0 | USD | CommercialPaper |
| `debt_noncurrent` | SOURCE_UNVALIDATED | 40152000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 43151000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 43151000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | PASS_DERIVED | 2999000000 | USD | LongTermDebtCurrent+CommercialPaper |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `depreciation_amortization` MISSING_OBSERVATION: NO_SOURCE_ROW_FOR_PERIOD, ALTERNATIVE_TAG_NOT_CONTRACT_EQUIVALENT
  - issuer tag `AmortizationOfIntangibleAssets` = 6000000000 USD (filed 2026-07-29); not contract equivalent
  - issuer tag `Depreciation` = 22000000000 USD (filed 2026-07-29); not contract equivalent
  - issuer tag `FinanceLeaseRightOfUseAssetAmortization` = 3408000000 USD (filed 2026-07-29); not contract equivalent
- `cash_and_equivalents` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_current` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` SOURCE_UNVALIDATED: SINGLE_SOURCE_OBSERVATION
- `debt_noncurrent` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_total_reported` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION

**Fiscal period ending 2024-06-30**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 245122000000 | USD | RevenueFromContractWithCustomerExcludingAssessedTax |
| `operating_income_ebit` | SOURCE_VALIDATED | 109433000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 88136000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | MISSING_OBSERVATION | — | USD | — |
| `capital_expenditure` | SOURCE_VALIDATED | 44477000000 | USD | PaymentsToAcquirePropertyPlantAndEquipment |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 7469000000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_UNVALIDATED | 18315000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 2249000000 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | SOURCE_UNVALIDATED | 6693000000 | USD | CommercialPaper |
| `debt_noncurrent` | SOURCE_UNVALIDATED | 42688000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 44937000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 44937000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | PASS_DERIVED | 8942000000 | USD | LongTermDebtCurrent+CommercialPaper |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `depreciation_amortization` MISSING_OBSERVATION: NO_SOURCE_ROW_FOR_PERIOD, ALTERNATIVE_TAG_NOT_CONTRACT_EQUIVALENT
  - issuer tag `AmortizationOfIntangibleAssets` = 4800000000 USD (filed 2026-07-29); not contract equivalent
  - issuer tag `Depreciation` = 15200000000 USD (filed 2026-07-29); not contract equivalent
  - issuer tag `FinanceLeaseRightOfUseAssetAmortization` = 1800000000 USD (filed 2026-07-29); not contract equivalent
- `cash_and_equivalents` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_current` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_noncurrent` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_total_reported` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION

### NVDA (CIK 0001045810)

- Overall: `SOURCE_PARTIALLY_VALIDATED`
- State counts: {"MISSING_OBSERVATION": 2, "PASS_DERIVED": 5, "SOURCE_UNVALIDATED": 18, "SOURCE_VALIDATED": 14}
- Source identity `approved_packet_sec_source_cash`: MATCH
- Blocker: `PUBLISHED_SEC_FACTS_NOT_IN_RELEASE`

**Fiscal period ending 2026-01-25**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 215938000000 | USD | Revenues |
| `operating_income_ebit` | SOURCE_VALIDATED | 130387000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 120067000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | SOURCE_VALIDATED | 2843000000 | USD | DepreciationDepletionAndAmortization |
| `capital_expenditure` | SOURCE_UNVALIDATED | 6042000000 | USD | PaymentsToAcquireProductiveAssets |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 24514000000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_VALIDATED | 10605000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 999000000 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | MISSING_OBSERVATION | — | USD | — |
| `debt_noncurrent` | SOURCE_VALIDATED | 7469000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 8468000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 8468000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | MISSING_OBSERVATION | — | USD | — |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `capital_expenditure` SOURCE_UNVALIDATED: SINGLE_SOURCE_OBSERVATION
- `debt_current` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` MISSING_OBSERVATION: NO_SOURCE_ROW_FOR_PERIOD
- `debt_total_reported` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_total` MISSING_OBSERVATION: MISSING_PART:debt_short_term_borrowings

**Fiscal period ending 2025-01-26**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 130497000000 | USD | Revenues |
| `operating_income_ebit` | SOURCE_VALIDATED | 81453000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 72880000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | SOURCE_VALIDATED | 1864000000 | USD | DepreciationDepletionAndAmortization |
| `capital_expenditure` | SOURCE_UNVALIDATED | 3236000000 | USD | PaymentsToAcquireProductiveAssets |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 24804000000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_UNVALIDATED | 8589000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 0 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | SOURCE_UNVALIDATED | 0 | USD | CommercialPaper |
| `debt_noncurrent` | SOURCE_UNVALIDATED | 8463000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 8463000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 8463000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | PASS_DERIVED | 0 | USD | LongTermDebtCurrent+CommercialPaper |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `capital_expenditure` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `cash_and_equivalents` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_current` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` SOURCE_UNVALIDATED: SINGLE_SOURCE_OBSERVATION
- `debt_noncurrent` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_total_reported` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION

**Fiscal period ending 2024-01-28**

| field | state | value | unit | concept |
| --- | --- | --- | --- | --- |
| `revenue` | SOURCE_VALIDATED | 60922000000 | USD | Revenues |
| `operating_income_ebit` | SOURCE_VALIDATED | 32972000000 | USD | OperatingIncomeLoss |
| `net_income_common` | SOURCE_UNVALIDATED | 29760000000 | USD | NetIncomeLoss |
| `depreciation_amortization` | SOURCE_VALIDATED | 1508000000 | USD | DepreciationDepletionAndAmortization |
| `capital_expenditure` | SOURCE_UNVALIDATED | 1069000000 | USD | PaymentsToAcquireProductiveAssets |
| `diluted_weighted_average_shares` | SOURCE_VALIDATED | 24940000000 | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| `cash_and_equivalents` | SOURCE_UNVALIDATED | 7280000000 | USD | CashAndCashEquivalentsAtCarryingValue |
| `debt_current` | SOURCE_UNVALIDATED | 1250000000 | USD | LongTermDebtCurrent |
| `debt_short_term_borrowings` | SOURCE_UNVALIDATED | 0 | USD | CommercialPaper |
| `debt_noncurrent` | SOURCE_UNVALIDATED | 8459000000 | USD | LongTermDebtNoncurrent |
| `debt_total_reported` | SOURCE_UNVALIDATED | 9709000000 | USD | LongTermDebt |
| `interest_bearing_debt_total` | PASS_DERIVED | 9709000000 | USD | LongTermDebtNoncurrent+LongTermDebtCurrent |
| `debt_short_term_total` | PASS_DERIVED | 1250000000 | USD | LongTermDebtCurrent+CommercialPaper |

Field notes:

- `net_income_common` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, NET_INCOME_TO_COMMON_UNADJUSTED, SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT
- `capital_expenditure` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `cash_and_equivalents` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_current` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_short_term_borrowings` SOURCE_UNVALIDATED: SINGLE_SOURCE_OBSERVATION
- `debt_noncurrent` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION
- `debt_total_reported` SOURCE_UNVALIDATED: SUPERSEDED_FILINGS_IGNORED, SINGLE_SOURCE_OBSERVATION

## Limitations

- serving_release_candidate: sampled manifest artifacts are not stored in R2 (3 of 3 missing)
- Aggregate manifest row counts are not evidence that an individual observation is correct.
- Storage access does not establish availability dates, freshness or redistribution rights.
- Stored timestamps and ETags are not financial observation dates.
