# ADR-0007: Filing Heading Recovery Heuristics

## Status
Accepted (updated v0.4.2)

## Context
SEC filings use inconsistent HTML for Item headers. Standard `<h2>` headings are rare. Most filings use `<div>`, `<p>`, `<b>`, or `<strong>` tags.

## Decision
The converter detects SEC Item header patterns in non-heading HTML elements and promotes them to markdown headings (`## Item N. Title`).

As of v0.4.2, the segmenter also filters likely TOC (Table of Contents) entries using a density heuristic: if 3+ Item matches cluster within 30 lines in the first 20% of the document, and later matches exist, the early cluster is treated as TOC and discarded.

## Alternatives Considered
1. **Parse only `<h1>`-`<h6>` tags**: Too restrictive — misses >80% of real filings.
2. **Promote ALL matching patterns**: Original approach — caused TOC bleed.
3. **Use page-number detection**: Too fragile — page numbers vary by filing.
4. **Machine learning classifier**: Overkill for current phase.

## Tradeoffs
- The density heuristic is simple but may fail on filings with unusual TOC placement
- Small documents (all sections in first 20%) are not affected (filter requires both early and late matches)
- The heuristic prefers precision over recall — better to miss a TOC filter than incorrectly discard real sections

## Consequences
- Most standard TOC layouts are correctly filtered
- Some edge cases remain (non-standard TOC placement, extremely short filings)
- Documented in `docs/context/wrapper_filing_edge_cases.md`

## Follow-up Work Required
- Test against real SEC filings to measure actual TOC bleed rate
- Consider scoring-based approach (content length, heading depth) for harder cases
