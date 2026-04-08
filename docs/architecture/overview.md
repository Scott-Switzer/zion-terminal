# Architecture Overview

## System Design

Zion Terminal is a layered financial data retrieval system:

```
CLI (click) → Orchestrator → Agents → Adapters → External APIs
                  ↓
            Validation Agent
                  ↓
            Output Formatter
```

## Layer Responsibilities

### CLI (`cli.py`)
- Click-based command interface
- Maps user commands to Orchestrator public methods
- **Never** accesses internal agents directly
- Supports `--strict` mode and `--no-validate` flag

### Orchestrator (`orchestrator/orchestrator.py`)
- Single entry point for all requests
- Routes NL queries through IntentParser → RetrievalAgent → ValidationAgent
- Exposes public methods: `get_quote`, `get_history`, `get_financials`, etc.
- `_fetch_and_validate()` is the shared pipeline — all public methods use it
- Strict mode: validation failures → `success=False`

### Intent Parser (`orchestrator/intent_parser.py`)
- Rule-based (first-class) + LLM fallback (optional)
- Extracts tickers, macro series, periods, intervals
- Word-boundary matching to prevent false positives (e.g. "meta" vs "metadata")
- Tags LLM-assisted results with `_llm_assisted=True`

### Retrieval Agent (`agents/retrieval/`)
- Routes tasks to correct adapter by source name
- Convenience methods: `fetch_quote`, `fetch_history`, etc.
- Auto-routing: infers adapter from task shape

### Adapters (`agents/retrieval/adapters/`)
- **YahooFinanceAdapter** — stock quotes, history, financials, company info
- **FREDAdapter** — macroeconomic time series (GDP, CPI, unemployment, etc.)
- **SECEdgarAdapter** — SEC filings, company facts, filing→markdown conversion

### Validation Agent (`agents/validation/`)
- Validates retrieval results: price bounds, volume, chronological order, etc.
- Validates synthesis results: accounting identity, gross profit math
- Machine-readable `ValidationCheck` objects

### Synthesis Agent (`agents/synthesis/`)
- Generates fictional companies with consistent financial statements
- Works without LLM (deterministic). LLM adds press release generation.

### Pipeline (`pipeline/`)
- `FilingConverter` — DOM-based HTML→markdown conversion
- `FilingSegmenter` — splits filings into sections by Item number
- `XBRLVerifier` — optional Arelle integration for XBRL validation

### Cache (`cache/`)
- diskcache-backed file caching with TTL
- Schema-versioned keys (bump `CACHE_SCHEMA_VERSION` on format changes)
- Cache keys include all relevant params (FRED dates, etc.)

## Data Flow

```
1. User: "zion history AAPL --period 6mo"
2. CLI calls: orc.get_history("AAPL", period="6mo")
3. Orchestrator calls: _fetch_and_validate(tasks=[...])
4. RetrievalAgent routes to YahooFinanceAdapter
5. Adapter checks cache → miss → calls yfinance API
6. Adapter returns RetrievalResult
7. ValidationAgent validates the data
8. Orchestrator returns OrchestratorResponse
9. CLI formats and prints via OutputFormatter
```

## Dependencies

### Required
- yfinance, fredapi, edgartools — data source libraries
- pandas, pydantic — data modeling
- click, rich — CLI interface
- diskcache, tenacity — caching and retry
- beautifulsoup4, lxml — HTML parsing

### Optional
- openai, tiktoken — `[openai]` extra for LLM features
- arelle-release — `[xbrl]` extra for XBRL validation
- pytest-benchmark — `[dev]` extra for benchmarks
