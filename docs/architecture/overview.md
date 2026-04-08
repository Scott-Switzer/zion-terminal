# Architecture Overview

## System Design

Zion Terminal is a layered financial data retrieval system with SEC EDGAR as
the primary source for company financial data:

```
CLI (click) → Orchestrator → Agents → Adapters → External APIs
                  ↓                       ↓
            Validation Agent      Filing Pipeline
                  ↓                (converter → segmenter → verifier)
            Output Formatter
```

## Source Hierarchy

| Data Domain | Primary | Verification | Fallback |
|---|---|---|---|
| Financial Statements | SEC EDGAR | Yahoo Finance | — |
| Filing Content | SEC EDGAR | — | — |
| Company Facts (XBRL) | SEC EDGAR | Arelle | — |
| Market Data | Yahoo Finance | — | — |
| Macro Data | FRED | — | — |

Codified in `models/source_roles.py`.

## Layer Responsibilities

### CLI (`cli.py`)
- Click-based command interface
- Maps user commands to Orchestrator public methods
- **Never** accesses internal agents directly
- Supports `--strict` mode and `--no-validate` flag
- `--source` flag for financials (sec or yahoo)

### Orchestrator (`orchestrator/orchestrator.py`)
- Single entry point for all requests
- Routes NL queries through IntentParser → RetrievalAgent → ValidationAgent
- Exposes public methods: `get_quote`, `get_history`, `get_financials`, etc.
- `get_financials()` defaults to SEC EDGAR, falls back to Yahoo
- `_fetch_and_validate()` is the shared pipeline — all public methods use it
- Strict mode: validation failures → `success=False`

### Intent Parser (`orchestrator/intent_parser.py`)
- Rule-based (first-class) + LLM fallback (optional)
- Extracts tickers, macro series, periods, intervals
- Word-boundary matching to prevent false positives
- Length-weighted keyword scoring to prefer specific intent matches
- Tags LLM-assisted results; `llm_assisted` surfaced in response metadata

### Retrieval Agent (`agents/retrieval/`)
- Routes tasks to correct adapter by source name
- Auto-routing: infers adapter from task shape
- Source role awareness via `models/source_roles.py`

### Adapters (`agents/retrieval/adapters/`)
- **YahooFinanceAdapter** — market data (quotes, history), company info convenience, financial verification
- **FREDAdapter** — macroeconomic time series (GDP, CPI, unemployment, etc.)
- **SECEdgarAdapter** — SEC filings, company facts, filing→markdown conversion, financial statements
  - Uses shared `FilingPipeline` for all conversion (no private converter)
  - Supports actions: filings, financials, company_facts, filing_markdown

### Validation Agent (`agents/validation/`)
- Validates retrieval results: price bounds, volume, chronological order
- Validates synthesis results: accounting identity, gross profit math
- Machine-readable `ValidationCheck` objects

### Synthesis Agent (`agents/synthesis/`)
- Generates fictional companies with consistent financial statements
- Deterministic: uses seeded `random.Random()` instance
- Same seed + same query → identical output (no-LLM mode)
- LLM adds optional press release generation (non-deterministic)

### Pipeline (`pipeline/`)
- **FilingPipeline** (`filing_pipeline.py`) — unified entry point: HTML → markdown → sections → verification hooks
- **FilingConverter** (`converter.py`) — DOM-based HTML→markdown; promotes SEC Item headers from div/p/b tags
- **FilingSegmenter** (`segmenter.py`) — splits filings into sections (heading-prefixed primary, plain-text fallback)
- **FilingVerifier** (`verification.py`) — structural + XBRL + cross-source verification
- **XBRLVerifier** (`xbrl.py`) — Arelle wrapper (optional dependency)

### Models (`models/`)
- **source_roles.py** — source hierarchy, SourceRole enum, SOURCE_ROLES matrix
- **responses.py** — OrchestratorResponse, RetrievalResult, ValidationResult, etc.
- **financial.py** — StockQuote, SECFiling, FinancialStatement, etc.
- **messages.py** — inter-agent message format

### Cache (`cache/`)
- diskcache-backed file caching with TTL
- Schema-versioned keys (bump `CACHE_SCHEMA_VERSION` on format changes)

## Data Flow: SEC Filing Pipeline

```
1. User: "zion filing-markdown AAPL --form 10-K"
2. CLI calls: orc.get_filing_markdown("AAPL", form="10-K")
3. Orchestrator calls: _fetch_and_validate(tasks=[...])
4. RetrievalAgent routes to SECEdgarAdapter
5. Adapter fetches raw HTML from SEC EDGAR
6. Adapter calls FilingPipeline.process(html, ...)
7. Pipeline: converter → segmenter → verification hooks
8. Returns FilingPipelineResult with structured metadata
9. Adapter wraps in RetrievalResult with pipeline_metadata
10. ValidationAgent validates
11. Orchestrator returns OrchestratorResponse
12. CLI formats and prints
```

## Data Flow: Financial Statements (SEC-first)

```
1. User: "zion financials AAPL --statement balance"
2. CLI calls: orc.get_financials("AAPL", statement_type="balance", source="sec")
3. If SEC available: routes to SECEdgarAdapter.fetch(action="financials")
4. If SEC unavailable or source="yahoo": routes to YahooFinanceAdapter
5. Response metadata includes source_preference and source_role
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
