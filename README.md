# Zion Terminal

Unified financial data retrieval with validation, caching, and synthetic data generation.

## What It Does

Zion Terminal fetches financial data from multiple sources through a single interface:

- **Financial Statements** — SEC EDGAR is the primary source; Yahoo Finance as fallback
- **Stock Data** — quotes, historical prices, company info (Yahoo Finance)
- **SEC Filings** — filing lists, filing-to-markdown conversion, XBRL company facts (SEC EDGAR)
- **Macro Data** — GDP, CPI, unemployment, Fed Funds rate, 40+ indicators (FRED)
- **Synthetic Data** — deterministic generation of fictional companies with consistent financials
- **Validation** — every data path runs through validation checks; strict mode rejects invalid data

The system works **without any LLM or API key** for Yahoo Finance data. FRED requires a [free API key](https://fred.stlouisfed.org/docs/api/api_key.html). SEC EDGAR requires an identity string.

## Installation

```bash
# Core install (Yahoo Finance works immediately)
pip install -e .

# With LLM support (optional — for ambiguous query parsing)
pip install -e ".[openai]"      # OpenAI
pip install -e ".[ollama]"      # Ollama (local)

# With XBRL validation (optional — requires Arelle)
pip install -e ".[xbrl]"

# Development (tests, linting, benchmarks)
pip install -e ".[dev]"
```

## Quick Start

```bash
# Stock quote
zion quote AAPL

# Historical data
zion history TSLA --period 6mo --interval 1wk

# Financial statements
zion financials MSFT --statement balance --quarterly          # SEC (default)
zion financials MSFT --statement income --source yahoo         # Yahoo fallback

# SEC filings
zion filings AAPL --form 10-K --limit 5

# Filing as markdown (experimental)
zion filing-markdown AAPL --form 10-K

# XBRL company facts
zion company-facts AAPL

# Macro data
zion macro GDP
zion macro CPI --start 2020-01-01 --end 2024-12-31

# Company info
zion info NVDA

# Natural language query
zion query "Show me Apple's quarterly cash flow"

# Synthetic company
zion synthesis

# Strict mode (fail on validation errors)
zion --strict quote AAPL
```

## Configuration

Create a `.env` file in the project root:

```env
# Required for FRED data
FRED_API_KEY=your_fred_key_here

# Required for SEC data
EDGAR_IDENTITY=Your Name your.email@example.com

# Optional: LLM for ambiguous query parsing
LLM_PROVIDER=none              # none | openai | ollama
OPENAI_API_KEY=                # only if LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o-mini       # default model
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.1

# Optional: cache configuration
CACHE_DIR=.cache/zion
CACHE_TTL_SECONDS=3600
```

## Architecture

```
CLI (click) → Orchestrator → Agents → Adapters → External APIs
                  ↓
            Validation Agent
                  ↓
            Output Formatter
```

**Key design principle:** The CLI never accesses internal agents directly. All data flows through the Orchestrator's public API (`get_quote`, `get_history`, etc.), which enforces validation and strict mode.

See `docs/architecture/overview.md` for details.

## Project Structure

```
src/zion_terminal/
├── cli.py                          # Click CLI (all commands)
├── config/                         # Settings from .env
├── orchestrator/
│   ├── orchestrator.py             # Public API: get_quote, get_history, etc.
│   └── intent_parser.py            # NL → structured tasks (rule-based + LLM fallback)
├── agents/
│   ├── retrieval/
│   │   ├── agent.py                # Routes tasks to adapters
│   │   ├── retry.py                # Shared retry config (broad exception coverage)
│   │   ├── base_adapter.py         # Abstract adapter with caching
│   │   └── adapters/
│   │       ├── yahoo_finance.py    # Quotes, history, financials, info
│   │       ├── fred.py             # FRED macro data
│   │       └── sec_edgar.py        # SEC filings, facts, filing→markdown
│   ├── validation/agent.py         # Data quality checks
│   └── synthesis/agent.py          # Synthetic company generation
├── pipeline/
│   ├── converter.py                # DOM-based HTML→markdown (BeautifulSoup)
│   ├── segmenter.py                # 10-K/10-Q section segmentation
│   └── xbrl.py                     # Arelle XBRL verification (optional dep)
├── cache/cache_manager.py          # Versioned diskcache with TTL
├── models/                         # Pydantic data models
├── outputs/formatter.py            # Markdown, JSON, CSV, table output
└── providers/base.py               # LLM abstraction (OpenAI, Ollama, None)

tests/
├── unit/                           # 300+ unit tests
├── benchmarks/                     # Parser and converter benchmarks
├── fixtures/                       # Static test corpus
└── integration/                    # Network-dependent tests

docs/
├── architecture/overview.md        # System design
├── decisions/                      # Architecture Decision Records (ADRs)
├── context/project-status.md       # Current status
└── runbooks/                       # How-to guides
```

## Validation

All retrieved data passes through the Validation Agent:

- **Price bounds** — stock prices checked against [0, 1M] range
- **Volume non-negative** — volumes must be ≥ 0
- **Source attribution** — every data item must have a source field
- **Chronological order** — time series must be sorted
- **Income statement math** — revenue − COGS = gross profit
- **Accounting identity** — assets = liabilities + equity (synthesis)
- **Cash reconciliation** — balance sheet cash = cash flow ending cash (synthesis)

**Strict mode** (`--strict`) makes validation failures terminate the request with `success=False`.

## Experimental Features

These features work but may have edge cases:

- **Filing→markdown conversion** — DOM-based (BeautifulSoup/lxml), works well on standard 10-K/10-Q formats. Complex or unusual filings may not convert perfectly.
- **Section segmentation** — splits filings by Item number. Requires headers to follow standard formatting.
- **XBRL verification** — thin Arelle wrapper. Requires `pip install zion-terminal[xbrl]`. Not yet integrated into the main validation pipeline.
- **LLM-assisted parsing** — optional fallback when rule-based parser can't interpret a query. Results are tagged with `_llm_assisted=True`.

## Testing

```bash
# Unit tests (offline, fast)
pytest tests/unit/ -v

# Benchmarks
pytest tests/benchmarks/ --benchmark-only

# Full corpus accuracy test
pytest tests/benchmarks/test_parser_benchmark.py::TestParserCorpusAccuracy -v

# Integration tests (requires network + API keys)
pytest tests/integration/ -v -m integration

# All tests except integration
pytest tests/ -m "not integration"
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Lint
ruff check src/ tests/

# Adding a new data source
# See docs/runbooks/adding-a-data-source.md
```

## Design Decisions

See `docs/decisions/` for Architecture Decision Records:

- [ADR-001](docs/decisions/001-cli-uses-orchestrator-public-api.md) — CLI must use orchestrator public API
- [ADR-002](docs/decisions/002-cache-versioning.md) — Cache key schema versioning
- [ADR-003](docs/decisions/003-strict-validation-mode.md) — Strict validation mode
- [ADR-004](docs/decisions/004-arelle-optional-dependency.md) — Arelle as optional XBRL dependency
- [ADR-005](docs/decisions/005-word-boundary-matching.md) — Word-boundary matching for company names

## License

MIT
