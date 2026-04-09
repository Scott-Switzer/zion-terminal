# Benchmark Report: Latest

**Run date:** 2026-04-09
**Commit:** 5d692ae
**Version:** 0.8.2
**Python:** 3.12.8
**Arelle:** Not installed

---

## Test Suite

| Metric | Value |
|---|---|
| Total tests | 402 |
| Passed | 402 |
| Failed | 0 |

---

## Fixture Processing

| Fixture | Size (KB) | Success | Sections | Converter | Duration (ms) | Compression |
|---|---|---|---|---|---|---|
| `sample_filing_scale_mismatch.html` | 0.7 | Yes | 1 | dom | 109.8 | 0.652 |
| `sample_filing_with_tables.html` | 0.7 | Yes | 1 | dom | 1.3 | 0.669 |
| `sample_html_filing.html` | 2.1 | Yes | 5 | dom | 1.8 | 0.774 |
| `sample_html_filing_div_headers.html` | 1.3 | Yes | 5 | dom | 1.2 | 0.652 |
| `sample_html_filing_table_toc.html` | 3.0 | Yes | 9 | dom | 3.0 | 0.723 |
| `sample_html_filing_wrapper.html` | 1.4 | Yes | 1 | dom | 1.0 | 0.82 |
| `toc_heavy_filing.html` | 1.8 | Yes | 5 | dom | 1.7 | 0.754 |

---

## Parser Accuracy

**Corpus:** 109 queries, **Accuracy:** 100.0%

| Category | Count | Correct | Accuracy |
|---|---|---|---|
| company_facts | 9 | 9 | 100.0% |
| company_info | 8 | 8 | 100.0% |
| filing_markdown | 10 | 10 | 100.0% |
| filings | 8 | 8 | 100.0% |
| financials | 12 | 12 | 100.0% |
| history | 7 | 7 | 100.0% |
| macro | 15 | 15 | 100.0% |
| multi | 6 | 6 | 100.0% |
| quote | 29 | 29 | 100.0% |
| synthesis | 5 | 5 | 100.0% |

---

## Notes

- All benchmarks run against local fixtures — no live API calls
- Company-facts reconciliation ran on 2 fixtures (2 passed) — does NOT require Arelle
- Arelle not installed — XBRL instance validation unavailable (company-facts reconciliation still works)
- Results are real measurements from the current codebase
