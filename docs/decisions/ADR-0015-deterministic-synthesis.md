# ADR-0015: Deterministic Synthesis

## Status
Accepted

## Context
The synthesis agent used `random.choice()`, `random.randint()`, etc. with the module-level RNG, making output non-reproducible.

## Decision
Synthesis uses an instance-level `random.Random()` seeded from:
1. An explicit `seed` parameter (highest priority)
2. A hash of the query string (default, for implicit reproducibility)

## Consequences
- Same seed + same query → identical output (in no-LLM mode)
- LLM-generated content (press releases) remains non-deterministic
- Tests can assert exact equality of repeated runs
- Different seeds produce different synthetic companies
