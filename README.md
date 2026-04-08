# Zion Terminal

**One interface to pull real financial data and generate realistic synthetic financial data.**

> **Status:** Proof of concept. Under active development. Not production-ready.

---

## Why This Project Exists

Financial data work is full of friction that has nothing to do with actual analysis.

**Getting data is a mess.** If you want stock prices you use one API. Macro data like interest rates? A different one. SEC filings? A third. Each has its own auth model, its own schema, its own rate limits and failure modes. You end up writing and maintaining glue code for every single source. Most of the time you are plumbing, not analyzing.

**Realistic fake data does not exist.** Say you are building a model, testing a pipeline, or demoing a product. You need data that looks real: a company with quarterly earnings, SEC filings, press releases, where the numbers actually add up across documents. You cannot just use real company data (licensing, compliance). Random numbers will not cut it because nothing will be consistent. There is no off-the-shelf solution for this.

Zion Terminal addresses both problems through a shared agent architecture. Small specialized LLM-powered agents each handle one job (fetch data, generate a document, check consistency). An orchestrator decides who does what. You can swap, test, or improve any piece without breaking the rest.

---

## Phased Delivery

This project is split into two phases. Phase 1 is the current focus.

### Phase 1: Unified Data Retrieval (current PoC)

A single natural-language query interface that pulls and caches financial data from public sources. Instead of juggling five APIs you ask one system in plain English and get clean structured data back.

### Phase 2: Synthetic Entity Engine (planned)

A generation layer that produces semi-fictional public companies with internally consistent financial statements, SEC filings, press releases, and analyst coverage. Every generated document passes through a validation agent before anything is served downstream.

---

## Who Would Use This

| Audience | Why |
|---|---|
| **Quants and researchers** | Spend less time on data plumbing, more on analysis |
| **ML engineers** | Realistic labeled training data without licensing headaches |
| **Fintech teams** | Demo and test against believable financial data, no compliance risk |
| **LLM application builders** | Structured financial context to feed into models |
| **Students and educators** | Accessible sandbox for financial data exploration |

---

## Architecture

```
                         ┌──────────────┐
               query ──▶ │ Orchestrator │ ◀── config / routing rules
                         └──────┬───────┘
                                │
                 ┌──────────────┼──────────────┐
                 ▼              ▼              ▼
        ┌────────────┐  ┌────────────┐  ┌────────────┐
        │  Retrieval  │  │  Synthesis  │  │ Validation │
        │   Agent     │  │   Agent     │  │   Agent    │
        └──────┬─────┘  └──────┬─────┘  └──────┬─────┘
               │               │               │
        data sources     LLM generation    rule engine
        + cache layer    + templates       + consistency
```

**Design principles:**

- Each agent is independently testable and replaceable.
- The orchestrator knows *what* to call, not *how* each agent works.
- All inter-agent communication uses a standardized message format.
- Components are modular so the team can work on agents in parallel.

---

## Core Components

### 1. Unified Data Retrieval Layer (Phase 1)

Accepts a natural-language query. Resolves it to one or more data sources. Handles errors and rate limits. Caches results. Returns clean structured output.

**Example:**

```
"Pull NVDA's quarterly financials and the current Fed Funds rate."
```

**What happens under the hood:**

1. The orchestrator parses intent and routes to the retrieval agent.
2. The retrieval agent selects the right source adapters (SEC EDGAR for financials, FRED for the Fed Funds rate).
3. Each adapter fetches, normalizes, and caches the response.
4. Results are merged into a single standardized output.

**Supported data sources** (planned and in progress):

| Category | Sources |
|---|---|
| **Equities** | Yahoo Finance, Alpha Vantage, Polygon |
| **Macro indicators** | FRED (Federal Reserve Economic Data) |
| **SEC filings** | EDGAR (10-K, 10-Q, 8-K, proxy statements) |
| **Planned** | Earnings call transcripts, FOMC minutes, global indices |

### 2. Synthetic Entity Engine (Phase 2)

Generates semi-fictional public companies with internally consistent financial documents.

**Use cases:**

- Stress-testing data pipelines without licensing real data.
- Training and evaluating ML models on diverse labeled financial scenarios.
- Building demos and prototypes without compliance concerns.
- Creating controlled test environments where you define the financial narrative.

**Generated document types:**

| Document | Description |
|---|---|
| **Income Statement** | Revenue, COGS, operating expenses, net income |
| **Balance Sheet** | Assets, liabilities, equity. Period-over-period consistent. |
| **Cash Flow Statement** | Operating, investing, financing. Reconciled to balance sheet. |
| **10-K / 10-Q filings** | Full-text SEC-style filings with embedded financial tables |
| **8-K filings** | Material event disclosures (earnings, M&A, leadership changes) |
| **Press releases** | Earnings announcements, guidance updates |
| **Analyst notes** | Third-party-style coverage with target prices and ratings |

Every generated document passes through the validation agent before it is served.

### 3. Orchestrator

The orchestrator is the single entry point for all requests.

- Parses incoming requests (natural language or structured).
- Determines which agents to invoke and in what order.
- Manages coordination for multi-step tasks (e.g. generate a company then validate all its documents).
- Returns a unified response regardless of which agents were involved.

### 4. Validation Agent

Enforces correctness across all generated and retrieved content.

- **Field-level checks.** Data types, required fields, numeric bounds.
- **Intra-document consistency.** Net income = revenue minus expenses.
- **Cross-document consistency.** Cash on the balance sheet matches the ending balance on the cash flow statement. 10-K narrative references match the embedded tables.
- **Temporal consistency.** Year-over-year figures tell a coherent story.

Documents that fail validation are rejected with specific error details so the synthesis agent can regenerate them.

---

## Output Formats

All outputs are optimized for LLM consumption and data pipeline integration.

| Format | Use Case |
|---|---|
| **Markdown** | LLM-friendly. Human-readable reports and filings. |
| **CSV** | Tabular data. Direct import into pandas, spreadsheets, databases. |
| **JSON** | Structured metadata. API responses. Programmatic access. |

---

## Project Structure

```
zion-terminal/
├── src/
│   ├── orchestrator/       # Request routing and agent coordination
│   ├── agents/
│   │   ├── retrieval/      # Data retrieval agent + source adapters
│   │   ├── synthesis/      # Synthetic entity and document generation
│   │   └── validation/     # Consistency and correctness checks
│   ├── cache/              # Caching layer (query results, generated docs)
│   ├── models/             # Shared data models and schemas
│   └── outputs/            # Output formatters (md, csv, json)
├── tests/
│   ├── unit/
│   └── integration/
├── config/                 # Source configs, agent parameters, prompts
├── docs/                   # Extended documentation
├── README.md
├── pyproject.toml
├── LICENSE
└── .env.example
```

---

## Getting Started

> Full setup instructions will be added as the prototype matures.

**Prerequisites:**

- Python 3.11+
- API keys for the data sources you want to use (see `.env.example`)
- An OpenAI-compatible API key for the LLM-powered agents

```bash
git clone https://github.com/zion-terminal/zion-terminal.git
cd zion-terminal
pip install -e ".[dev]"
cp .env.example .env
```

Add your API keys to `.env` and you are ready to go.

---

## Roadmap

### Phase 1: Data Retrieval (complete)

- [x] Project scaffolding and architecture design
- [x] Retrieval agent: Yahoo Finance adapter (quotes, history, financials, company info)
- [x] Retrieval agent: FRED adapter (30+ macro indicators with aliases)
- [x] Retrieval agent: SEC EDGAR adapter (filings, XBRL financials, company facts)
- [x] Caching layer (diskcache with TTL support)
- [x] Orchestrator: NLP intent parsing + agent dispatch + response merging
- [x] CLI interface (query, quote, financials, macro, filings, history, synthesize, sources)
- [x] Output formatting (markdown, csv, json)
- [x] Validation agent: field-level and cross-document checks
- [x] Unit tests (68 tests) and integration tests (6 tests)

### Phase 2: Synthetic Entities (in progress)

- [x] Synthesis agent: financial statement generation (income, balance sheet, cash flow)
- [x] Validation agent: cross-document consistency checks
- [x] End-to-end entity generation pipeline
- [ ] Synthesis agent: SEC filing generation (10-K, 10-Q, 8-K)
- [ ] Synthesis agent: press releases and analyst notes (LLM-powered)

### Future

- [ ] Web UI
- [ ] Additional data sources (earnings transcripts, FOMC minutes, global indices)
- [ ] Plugin system for custom adapters

---

## License

[MIT](LICENSE)
