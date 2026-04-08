# Deterministic Synthesis

## Design
The synthesis agent produces reproducible output from the same inputs.

## How It Works

### Seeded RNG
- Each `generate()` call uses an instance-level `random.Random()` object
- If `params["seed"]` is provided, it seeds the RNG with that value
- If no explicit seed, the query string is hashed to produce a deterministic seed
- All randomness (`choice`, `randint`, `uniform`) goes through `self._rng`

### Reproducibility Guarantee (no-LLM mode)
- Same seed + same query → identical output
- Same query (no explicit seed) → identical output (query hash is deterministic)
- Different seeds → different output

### LLM Mode
- With an LLM provider, the press release generation is NOT deterministic
- Temperature, sampling, and model behavior introduce non-determinism
- Determinism applies only to the financial data generation (income statement, balance sheet, cash flow)
- The press release is an optional addition and does not affect financial data

## Testing
- `test_same_seed_same_output` — repeated runs with seed=42 produce identical results
- `test_different_seeds_differ` — different seeds produce different companies
- `test_same_query_reproducible_without_explicit_seed` — query-derived seed is stable
