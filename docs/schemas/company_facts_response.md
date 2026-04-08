# Schema: CompanyFactsResponse

`company_facts` is a data shape returned by the SEC EDGAR adapter when `action="company_facts"` is requested. It is part of the `data` field in an `AgentResponse` with `agent="retrieval"`.

The shape is defined by the SEC EDGAR adapter's `_format_company_facts()` method in `src/zion_terminal/agents/retrieval/adapters/sec_edgar.py`.

---

## Top-level Fields

| Field | Type | Description |
|---|---|---|
| `ticker` | `str` | Equity ticker symbol (e.g. `"AAPL"`). |
| `type` | `"company_facts"` | Literal discriminator string. Always `"company_facts"`. |
| `company_name` | `str` | Full legal company name from SEC EDGAR (e.g. `"Apple Inc."`). |
| `cik` | `str` | SEC Central Index Key, zero-padded to 10 digits (e.g. `"0000320193"`). |
| `source` | `"sec_edgar"` | Always `"sec_edgar"` for this response type. |
| `facts_count` | `int` | Total number of XBRL facts available for this company in the SEC API. |
| `sample_facts` | `list[dict]` | Representative sample of facts (up to 20). Present when XBRL data is available. See sub-schema below. |
| `facts_summary` | `str \| None` | Fallback string summary when `sample_facts` cannot be constructed. `None` when `sample_facts` is present. |

Exactly one of `sample_facts` or `facts_summary` will be non-null/non-empty.

---

## Sub-schema: Sample Fact Entry

Each entry in `sample_facts` represents a single XBRL fact:

| Field | Type | Description |
|---|---|---|
| `concept` | `str` | XBRL concept name (e.g. `"us-gaap/Revenues"`). |
| `label` | `str` | Human-readable label (e.g. `"Revenues"`). |
| `value` | `number \| str` | Reported value. Numbers are unscaled (i.e. actual dollars, not millions). |
| `unit` | `str` | Unit of measure (e.g. `"USD"`, `"shares"`). |
| `period` | `str` | Reporting period (e.g. `"2024-09-28"` for instant, `"2023-10-01/2024-09-28"` for duration). |
| `form` | `str` | SEC form type in which this fact was filed (e.g. `"10-K"`, `"10-Q"`). |

---

## Example JSON: Full Response with sample_facts

```json
{
  "ticker": "AAPL",
  "type": "company_facts",
  "company_name": "Apple Inc.",
  "cik": "0000320193",
  "source": "sec_edgar",
  "facts_count": 3847,
  "sample_facts": [
    {
      "concept": "us-gaap/Revenues",
      "label": "Revenues",
      "value": 391035000000,
      "unit": "USD",
      "period": "2022-10-02/2023-09-30",
      "form": "10-K"
    },
    {
      "concept": "us-gaap/NetIncomeLoss",
      "label": "Net Income (Loss) Attributable to Parent",
      "value": 96995000000,
      "unit": "USD",
      "period": "2022-10-02/2023-09-30",
      "form": "10-K"
    },
    {
      "concept": "us-gaap/EarningsPerShareBasic",
      "label": "Earnings Per Share, Basic",
      "value": 6.16,
      "unit": "USD/shares",
      "period": "2022-10-02/2023-09-30",
      "form": "10-K"
    },
    {
      "concept": "us-gaap/Assets",
      "label": "Assets",
      "value": 352583000000,
      "unit": "USD",
      "period": "2023-09-30",
      "form": "10-K"
    },
    {
      "concept": "us-gaap/CommonStockSharesOutstanding",
      "label": "Common Stock, Shares, Outstanding",
      "value": 15550061000,
      "unit": "shares",
      "period": "2023-09-30",
      "form": "10-K"
    }
  ],
  "facts_summary": null
}
```

---

## Example JSON: Fallback with facts_summary

```json
{
  "ticker": "TSLA",
  "type": "company_facts",
  "company_name": "Tesla, Inc.",
  "cik": "0001318605",
  "source": "sec_edgar",
  "facts_count": 0,
  "sample_facts": [],
  "facts_summary": "XBRL facts unavailable for TSLA — SEC API returned empty taxonomy. CIK resolved: 0001318605."
}
```

---

## Notes

- `facts_count` reflects the total facts in SEC's `companyfacts` endpoint, not the number returned in `sample_facts`.
- `sample_facts` is capped at 20 entries by default. The selection favors the most recently filed annual (`10-K`) facts.
- `value` for monetary facts is in absolute dollars (not millions). Downstream formatters apply scaling for display.
- The `_format_company_facts_md()` method in `sec_edgar.py` converts this structure into a human-readable markdown table for CLI output.
- This schema corresponds to the SEC EDGAR Company Facts API: `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`
