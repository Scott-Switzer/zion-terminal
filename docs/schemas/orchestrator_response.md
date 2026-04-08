# Schema: OrchestratorResponse

`OrchestratorResponse` is the top-level return type of all public methods on `Orchestrator` (defined in `src/zion_terminal/orchestrator/orchestrator.py`). Every CLI command and programmatic call returns this shape.

---

## Top-level Fields

| Field | Type | Description |
|---|---|---|
| `success` | `bool` | `True` if at least one agent produced usable results. `False` if all agents failed. |
| `query` | `str` | The original query string passed to the orchestrator. |
| `intent` | `str` | Resolved intent (e.g. `"equity_price"`, `"sec_filing"`, `"macro_series"`, `"company_facts"`). |
| `results` | `list[AgentResponse]` | List of per-agent responses. See sub-schema below. |
| `errors` | `list[str]` | Top-level errors not associated with a specific agent. |
| `metadata` | `dict` | Orchestration metadata. Fields vary by intent — see below. |

---

## Sub-schema: AgentResponse

Each entry in `results` has this shape:

| Field | Type | Description |
|---|---|---|
| `agent` | `str` | Agent name: `"retrieval"`, `"synthesis"`, `"validation"`. |
| `success` | `bool` | Whether this agent completed without error. |
| `data` | `Any \| None` | Agent-specific payload. For retrieval: raw data dict. For synthesis: formatted string. For validation: `ValidationResult`. |
| `sources_used` | `list[str]` | Data sources accessed (e.g. `["sec_edgar"]`, `["yahoo_finance", "sec_edgar"]`). |
| `cached` | `bool` | Whether the result was served from cache. |
| `errors` | `list[str]` | Per-agent errors. |

---

## `metadata` Fields by Intent

The `metadata` dict may contain the following keys depending on the resolved intent:

| Key | Type | Present When | Description |
|---|---|---|---|
| `tickers` | `list[str]` | Equity/SEC intents | Tickers resolved from the query. |
| `macro_series` | `list[str]` | Macro intent | FRED series IDs resolved. |
| `tasks_count` | `int` | Always | Number of retrieval tasks dispatched. |
| `llm_provider` | `str \| None` | LLM-assisted responses | LLM provider used (e.g. `"openai"`). `None` if no LLM. |
| `llm_assisted` | `bool` | Always | Whether LLM was used for synthesis or intent parsing. |
| `source_preference` | `str \| None` | When `--source` flag used | Explicit source override (e.g. `"yahoo"`, `"sec"`). |
| `source_role` | `str \| None` | Always | Role of the primary source (e.g. `"primary"`, `"verification"`, `"fallback"`). |

---

## Example: Equity Price Query

```json
{
  "success": true,
  "query": "AAPL stock price",
  "intent": "equity_price",
  "results": [
    {
      "agent": "retrieval",
      "success": true,
      "data": {
        "ticker": "AAPL",
        "price": 213.49,
        "currency": "USD",
        "timestamp": "2024-11-01T16:00:00Z"
      },
      "sources_used": ["yahoo_finance"],
      "cached": false,
      "errors": []
    },
    {
      "agent": "synthesis",
      "success": true,
      "data": "AAPL closed at $213.49 on 2024-11-01.",
      "sources_used": [],
      "cached": false,
      "errors": []
    },
    {
      "agent": "validation",
      "success": true,
      "data": {
        "agent": "validation",
        "success": true,
        "status": "passed",
        "checks_run": 3,
        "checks_passed": 3,
        "checks_warned": 0,
        "checks_failed": 0,
        "checks_skipped": 0,
        "checks_unavailable": 0,
        "details": [],
        "warnings": []
      },
      "sources_used": [],
      "cached": false,
      "errors": []
    }
  ],
  "errors": [],
  "metadata": {
    "tickers": ["AAPL"],
    "tasks_count": 1,
    "llm_provider": null,
    "llm_assisted": false,
    "source_preference": null,
    "source_role": "primary"
  }
}
```

---

## Example: SEC Filing Query

```json
{
  "success": true,
  "query": "MSFT 10-K latest",
  "intent": "sec_filing",
  "results": [
    {
      "agent": "retrieval",
      "success": true,
      "data": {
        "ticker": "MSFT",
        "form": "10-K",
        "filing_date": "2024-07-30",
        "markdown_char_count": 428000,
        "section_count": 17
      },
      "sources_used": ["sec_edgar"],
      "cached": false,
      "errors": []
    }
  ],
  "errors": [],
  "metadata": {
    "tickers": ["MSFT"],
    "tasks_count": 1,
    "llm_provider": null,
    "llm_assisted": false,
    "source_preference": null,
    "source_role": "primary"
  }
}
```

---

## Example: Total Failure

```json
{
  "success": false,
  "query": "ZZZZ stock price",
  "intent": "equity_price",
  "results": [],
  "errors": ["No adapter could resolve ticker 'ZZZZ'"],
  "metadata": {
    "tickers": ["ZZZZ"],
    "tasks_count": 1,
    "llm_assisted": false,
    "source_preference": null,
    "source_role": null
  }
}
```

---

## Notes

- `results` may be empty if all agents failed before producing output.
- `success` at the top level reflects whether the orchestration produced actionable data. Agent-level `success` fields are independent.
- `metadata.llm_assisted` is `False` in no-LLM mode regardless of provider configuration.
- The `intent` field is set to `"unknown"` when the parser could not assign a confident intent.
