# Fundamental Metrics V1

These names are canonical in Zion. Aliases are parser-only and do not change metric meaning (`sales` → `revenue`, `op margin` → `operating_margin`, `net earnings` → `net_income`).

| Metric | Definition | Unit | Periods | Source/calculation |
|---|---|---|---|---|
| `revenue` | Consolidated company revenue | USD | annual, quarterly | PPE published SEC fact |
| `cost_of_revenue` | Consolidated cost of sales/services | USD | annual, quarterly | PPE published SEC fact |
| `gross_profit` | Revenue less cost of revenue, or reported consolidated gross profit | USD | annual, quarterly | PPE published SEC fact |
| `operating_income` | Consolidated operating income/loss | USD | annual, quarterly | PPE published SEC fact |
| `net_income` | Consolidated net income/loss | USD | annual, quarterly | PPE published SEC fact |
| `gross_margin` | `gross_profit / revenue` | ratio | annual, quarterly | deterministic calculation |
| `operating_margin` | `operating_income / revenue` | ratio | annual, quarterly | deterministic calculation |
| `net_margin` | `net_income / revenue` | ratio | annual, quarterly | deterministic calculation |
| `cash` | Cash and cash equivalents | USD | instant | PPE published SEC fact |
| `assets` | Consolidated total assets | USD | instant | PPE published SEC fact |
| `liabilities` | Consolidated total liabilities | USD | instant | PPE published SEC fact |
| `equity` | Consolidated stockholders' equity | USD | instant | PPE published SEC fact |
| `debt` | Selected reported long-term debt obligation | USD | instant | PPE published SEC fact |
| `shares_outstanding` | Common shares outstanding | shares | instant | PPE DEI fact |
| `eps_diluted` | Diluted earnings per share | USD/share | annual, quarterly | PPE published SEC fact |

## Selection policy

The read-model builder selects company-wide contexts only. Contexts containing segment or dimensional facts are excluded. Facts are keyed by metric, period type, and period end; the latest available filing deterministically wins for a duplicate key. A fact retains filing date, availability timestamp, form, accession, source tag, and context identifier.

Annual and quarterly observations are separate series. A request naming a period receives only that period; if no source observation exists it returns `PERIOD_NOT_AVAILABLE` rather than substituting another period. Bank-specific reporting may not provide industrial-company measures such as gross margin; absence is reported as unavailable.

## Point-in-time

A fact is eligible only when `available_at <= as_of`. A filing dated after `as_of` cannot contribute to fundamentals, calculations, comparisons, or evidence. Derived margins inherit the availability and provenance of their source filing.

Synthetic values use the producer's public financial artifacts and retain world version, producer revision, artifact, and QC certification metadata. Zion does not create synthetic truth.
