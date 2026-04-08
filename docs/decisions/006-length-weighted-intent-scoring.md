# ADR-006: Length-Weighted Intent Scoring

## Status
Accepted

## Context
The intent parser classified queries by counting how many keywords matched each intent.  When two intents had equal match counts (e.g., "filing" matching both `filings` and `filing_markdown`), the result was unpredictable — it depended on dict iteration order.

Generic single-word keywords like `"content"`, `"markdown"`, `"facts"` caused false routing for common queries.

## Decision
1. Removed overly generic keywords from `filing_markdown` (`"content"`, `"markdown"`, `"full text"`, `"document text"`) and `company_facts` (`"facts"`).
2. Changed scoring from count-based to length-weighted: each matching keyword adds its character length to the intent score. This naturally prefers longer, more-specific keyword phrases.
3. Added `"prices"` to the `quote` intent to prevent it losing to `history` (which already had `"prices"`).

## Consequences
- More-specific keyword matches win ties without needing explicit priority rules.
- The benchmark corpus accuracy remains at 100% (66/66 queries).
- Future keyword additions should prefer multi-word phrases over single words.
