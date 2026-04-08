# ADR-005: Word-Boundary Matching for Company Names

## Status
Accepted (v0.3.0)

## Context
The intent parser's company name → ticker mapping used naive substring
matching (`if name in lower`). This caused false positives:

- "metadata" matched "meta" → META ticker
- "boeing information" matched "go" → potential false routes
- "costco" contained "cost" → could interfere with partial matches

## Decision
Replace `if name in lower` with regex word-boundary matching:
```python
re.search(r"\b" + re.escape(name) + r"\b", text)
```

This ensures "meta" only matches when it appears as a complete word,
not as part of "metadata", "metamorphosis", etc.

## Consequences
- Eliminates false positive ticker extraction
- Marginal regex overhead (negligible — tested in benchmarks)
- Multi-word company names ("jp morgan") still work correctly
- Benchmarks track accuracy regression via fixture corpus
