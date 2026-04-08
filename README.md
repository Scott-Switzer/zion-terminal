# Zion Terminal

Unified financial data retrieval and synthetic data generation from the command line.

## What It Does (Honestly)

Zion Terminal pulls real financial data from **free, public sources** and provides it through a clean CLI and Python API. It also generates synthetic company data with mathematically consistent financial statements.

### Working Now

| Feature | Source | API Key Required |
|---------|--------|-----------------|
| Stock quotes (price, volume, market cap, etc.) | Yahoo Finance | No |
| Historical price data (OHLCV) | Yahoo Finance | No |
| Financial statements (income, balance, cash flow) | Yahoo Finance | No |
| Company info/profile | Yahoo Finance | No |
| SEC filings list (10-K, 10-Q, 8-K) | SEC EDGAR | `EDGAR_IDENTITY` (free) |
| Filing-to-markdown conversion | SEC EDGAR | `EDGAR_IDENTITY` (free) |
| XBRL company facts | SEC EDGAR | `EDGAR_IDENTITY` (free) |
| Macroeconomic data (GDP, CPI, rates, etc.) | FRED | `FRED_API_KEY` (free) |
| Synthetic company generation | Built-in | No |
| Output: Markdown, JSON, CSV | Built-in | No |

### Requires LLM (Optional)

- **Synthetic press releases** — generated with OpenAI or Ollama; skipped without LLM
- **Ambiguous query fallback** — natural language queries that the rule-based parser can't handle

### Not Implemented

- Alpha Vantage, Polygon, or Bloomberg data sources
- Real-time streaming or WebSocket feeds
- Portfolio tracking or position management
- Backtesting or strategy simulation

## Installation

```bash
# Core (no LLM required)
pip install -e .

# With OpenAI support
pip install -e ".[openai]"

# With Ollama support
pip install -e ".[ollama]"

# Development
pip install -e ".[dev]"
```

Requires Python 3.11+.

## Configuration

Copy `.env.example` to `.env` and fill in your values. **All keys are optional.**

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_PROVIDER` | No | `none` (default), `openai`, or `ollama` |
| `OPENAI_API_KEY` | Only if `LLM_PROVIDER=openai` | OpenAI API key |
| `OPENAI_MODEL` | No | Default: `gpt-4o-mini` |
| `OLLAMA_BASE_URL` | No | Default: `http://localhost:11434/v1` |
| `OLLAMA_MODEL` | No | Default: `llama3.1` |
| `FRED_API_KEY` | For macro data | Free from [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) |
| `EDGAR_IDENTITY` | For SEC data | Your name + email per [SEC policy](https://www.sec.gov/os/accessing-edgar-data) |

## CLI Usage

```bash
# Natural language query (no LLM needed for supported patterns)
zion query "Get AAPL stock price"
zion query "Show me Tesla quarterly financials" --format json

# Direct commands
zion quote AAPL
zion history AAPL --period 6mo --interval 1wk
zion financials AAPL --statement balance --quarterly
zion filings AAPL --form 10-K --limit 5
zion macro GDP
zion info AAPL
zion synthesis

# Output formats: markdown (default), json, csv
zion quote AAPL --format csv
```

Every flag shown above actually works. There are no decorative flags.

## Python API

```python
from zion_terminal.orchestrator.orchestrator import Orchestrator
from zion_terminal.outputs.formatter import format_response, OutputFormat

orc = Orchestrator()  # no API keys needed for basic stock data
resp = orc.query("Get AAPL stock price")
print(format_response(resp, OutputFormat.MARKDOWN))
orc.close()
```

## Architecture

```
src/zion_terminal/
├── __init__.py              # version
├── cli.py                   # Click CLI with real flags
├── config/
│   └── settings.py          # pydantic settings from .env
├── providers/
│   └── base.py              # LLM abstraction: NoLLM, OpenAI, Ollama
├── models/
│   ├── financial.py         # StockQuote, FinancialStatement, SECFiling, etc.
│   ├── messages.py          # Agent message types
│   └── responses.py         # AgentResponse, RetrievalResult, ValidationResult, etc.
├── cache/
│   └── cache_manager.py     # diskcache wrapper
├── agents/
│   ├── retrieval/
│   │   ├── agent.py         # Routes tasks to adapters
│   │   ├── base_adapter.py  # Adapter interface
│   │   └── adapters/
│   │       ├── yahoo_finance.py  # Quotes, history, financials, company info
│   │       ├── fred.py           # Macro data from FRED
│   │       └── sec_edgar.py      # Filings, XBRL facts, filing-to-markdown
│   ├── validation/
│   │   └── agent.py         # Machine-readable validation checks
│   └── synthesis/
│       └── agent.py         # Synthetic company generation
├── orchestrator/
│   ├── orchestrator.py      # Single entry point for all queries
│   └── intent_parser.py     # Rule-based NL parser (no LLM required)
└── outputs/
    └── formatter.py         # Markdown, JSON, CSV formatters
```

### Data Flow

```
User query → IntentParser (rule-based) → RetrievalAgent → Adapters → ValidationAgent → Formatter
                                                                              ↓
                                                                    OrchestratorResponse
```

### LLM Provider Abstraction

The system defaults to `LLM_PROVIDER=none`. All core retrieval, validation, and formatting works without any LLM. The LLM is only used for:

1. Synthetic press release generation (optional enhancement)
2. Ambiguous query parsing fallback (if rule-based parser fails)

Three providers are supported:
- **none** — default, no LLM calls, everything degrades gracefully
- **openai** — requires `OPENAI_API_KEY` and `pip install 'zion-terminal[openai]'`
- **ollama** — requires running Ollama server and `pip install 'zion-terminal[ollama]'`

## Testing

```bash
# Unit tests only (no network)
pytest -m "not integration"

# All tests including integration (requires network)
pytest

# With coverage
pytest --cov=zion_terminal
```

## Known Limitations

- Yahoo Finance uses an unofficial API that may break or rate-limit with heavy use.
- SEC EDGAR filing-to-markdown conversion is best-effort. Complex HTML tables may not convert cleanly.
- The intent parser uses regex and keyword matching. Unusual phrasing may not parse correctly. The LLM fallback helps but is optional.
- Synthetic data uses random generation seeded at call time. Results differ between runs.
- Validation checks are basic: price bounds, volume signs, income math, accounting identity, cash reconciliation. They don't catch all possible data quality issues.

## License

MIT
