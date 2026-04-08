# Wrapper Filing Edge Cases

## Known Edge Cases

### 1. Wrapper Filings
Some companies file "wrapper" 10-Ks that reference a separate annual report document. The wrapper itself contains minimal content — the real financial data is in the referenced document.

**Current handling:** Not explicitly detected. The converter will process whatever HTML it receives. If the wrapper is thin, the result will be thin.

**Future:** Detect wrapper filings by content length or lack of expected sections, and attempt to follow the reference.

### 2. Table of Contents (TOC)
Many filings include a Table of Contents page that lists "Item 1", "Item 1A", etc. as navigation links, not as actual section headers. If the converter promotes these to headings, the segmenter may create phantom sections.

**Current handling:** The converter only promotes Item headers in `<p>`, `<b>`, `<div>` tags. TOC entries that are `<td>` cells or `<a>` links are not promoted. This handles the majority of cases but edge cases remain.

**Risk:** A TOC with `<p><b>Item 1. Business</b></p>` entries could be confused with real section starts. The segmenter will split at both the TOC entry and the real section, creating a near-empty TOC-derived section followed by the real one.

### 3. Duplicate Item Headings
Some filings repeat Item headings in headers/footers on every page. After conversion, these could appear as multiple "Item 7" entries.

**Current handling:** The segmenter takes the first match for each Item number. Duplicate headings create additional split points but the content is still captured.

### 4. Non-Standard Numbering
A small percentage of filings (~10%) use non-standard heading formats or skip Item numbers.

**Current handling:** Returns as a single "full" document if no Item patterns match. This is a graceful degradation, not a failure.

## Test Fixtures
- `sample_html_filing.html` — standard headings
- `sample_html_filing_div_headers.html` — div/p/b headers
- `sample_html_filing_table_toc.html` — filing with TOC table
- `sample_html_filing_wrapper.html` — minimal wrapper filing (edge case)

## Open Questions
- How to detect and follow wrapper filing references?
- How to discriminate TOC entries from real section headers in edge cases?
- Should we merge near-empty sections that look like TOC artifacts?
