# Company Facts Output Notes

## Data Shape

The `company-facts` command returns XBRL facts from SEC EDGAR:

```json
{
  "ticker": "AAPL",
  "type": "company_facts",
  "company_name": "Apple Inc.",
  "cik": "0000320193",
  "facts_count": 1500,
  "sample_facts": [
    {"concept": "Revenue", "value": 394328000000, "period": "2024"},
    ...
  ],
  "source": "sec_edgar"
}
```

## Markdown Formatting

The markdown formatter renders company facts as:
- Header with ticker, company name, CIK, and total fact count
- Table of up to 15 sample facts (first 6 columns)
- Note about total vs shown count

## Machine Readability

Use `--format json` for programmatic access. The JSON output includes the full `sample_facts` array and `facts_count` for pagination awareness.

## Limitations
- Only the first 20 facts are fetched from edgartools (`df.head(20)`)
- If edgartools returns a non-dataframe format, a text summary is shown instead
- No filtering by concept, period, or unit yet
