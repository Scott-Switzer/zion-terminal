# Live Document Persistence Behavior

## How It Works

When `get_filing_markdown()` is called on the Orchestrator:

1. The filing is fetched via the SEC adapter
2. The HTML is processed through the filing pipeline (converter → segmenter → verifier)
3. **If the response is successful**, `_persist_filing_document()` is called
4. A `CleanedDocument` is created from the response data
5. The document is stored in `DocumentStore` (SQLite-backed)
6. The document can be retrieved later by `doc_id`

## What Is Persisted

| Field | Source |
|-------|--------|
| `doc_id` | `{TICKER}_{FORM}_{FILING_DATE}` |
| `ticker` | From the request |
| `form` | From the request |
| `filing_date` | From the response data |
| `markdown` | From `content_markdown` in response |
| `verification` | From `pipeline_metadata.verification` |
| `metadata` | From `pipeline_metadata` |

## When Persistence Does NOT Run

- When the response `success` is False (SEC error, parsing failure, etc.)
- When `persist=False` is passed to `get_filing_markdown()`
- When the response data has no `content_markdown`

## Proof

This behavior is tested at runtime by:
- `test_persist_creates_document_in_store` — verifies actual SQLite storage
- `test_get_filing_markdown_calls_persist` — verifies the live path calls persist
- `test_persist_does_not_run_on_failure` — verifies persistence skips on failure
- `test_doc_store_list_after_persist` — verifies listing works after storage

## Team Usage

```python
orc = Orchestrator()
response = orc.get_filing_markdown("AAPL", form="10-K")

# Document is already persisted — retrieve it later:
docs = orc.doc_store.list_docs(ticker="AAPL")
doc = orc.doc_store.get(docs[0]["doc_id"])
```
