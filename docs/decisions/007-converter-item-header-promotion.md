# ADR-007: Converter Promotes SEC Item Headers to Markdown Headings

## Status
Accepted

## Context
Real SEC filings rarely use `<h1>`–`<h4>` tags for Item headers.  Instead, they use `<div>`, `<p>`, `<b>`, or `<strong>` elements.  The converter only produced markdown headings from HTML heading tags, so the segmenter (which matched `## Item N`) found zero sections in most real filings.

## Decision
1. The converter now detects SEC Item header patterns (`Item N.` / `ITEM N`) inside `<p>`, `<b>`/`<strong>`, and `<div>` tags, and promotes them to `## ` markdown headings.
2. The segmenter gained a fallback pattern that matches plain-text `Item N` lines (no `#` prefix), activated only when no heading-prefixed patterns are found.

Detection uses a regex: `^\s*(?:ITEM|Item)\s+\d{1,2}[A-Ba-b]?\b[.:–—-]?\s*.{0,120}$`

## Consequences
- Real SEC filings now produce correctly segmented sections.
- The heading-prefix pattern is always preferred; the fallback only activates for documents where the converter could not promote headers.
- A new HTML fixture (`sample_html_filing_div_headers.html`) validates this behavior.
