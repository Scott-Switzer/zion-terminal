# Benchmark Semantics and Interpretation

## How To Read Benchmark Results

### Verification Status Per Fixture

| Status | What It Means |
|--------|--------------|
| `reconciled_pass` | Company-facts reconciliation ran and passed (≥80% match, exact period) |
| `reconciled_partial` | Reconciliation ran but match rate or period quality was insufficient |
| `structural_only` | No company facts provided — only structural checks ran |
| `failed` | Structural checks failed (markdown too short or empty) |

### Reconciliation Status Per Fixture

| Status | What It Means |
|--------|--------------|
| `reconciled_pass` | XBRL facts matched markdown facts in the same fiscal period |
| `no_company_facts` | No company facts were available for this fixture |
| `no_period_match` | Company facts existed but no matching fiscal period found |

### Important: Company-Facts Reconciliation vs Arelle

Benchmarks that show `reconciled_pass` used **company-facts-based reconciliation**,
which does NOT require Arelle. This is the primary verification method.

Arelle provides additional XBRL instance document validation but is not needed for
the core fact-matching verification. When the benchmark notes say "Arelle not
installed", this does NOT mean reconciliation is unavailable — it means XBRL
schema validation specifically is unavailable.

### Test Count Semantics

The benchmark runner uses `pytest -p no:benchmark` which excludes pytest-benchmark
fixtures. This means the count may differ from the full `pytest` count:
- **Benchmark count**: tests excluding benchmark fixtures
- **Full count**: all tests including benchmark fixtures (higher number)

Both are correct; they measure different things.
