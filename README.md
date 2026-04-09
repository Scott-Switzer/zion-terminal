# Zion Terminal

**Unified financial data retrieval, validation, and synthetic data generation.**

> **Status:** Proof of concept (v0.8.2). Under active development. Not production-ready.

---

## What It Does

Zion Terminal provides a single CLI and Python API to pull financial data from multiple public sources, validate it, and optionally generate realistic synthetic financial data.

**Data retrieval** — One command to get stock quotes, financial statements, SEC filings, and macroeconomic indicators. SEC EDGAR is the primary source for financial statements and filings; Yahoo Finance handles market data; FRED handles macro.

**Synthetic data** — Generate semi-fictional companies with internally consistent income statements, balance sheets, and cash flow statements. Deterministic by default (same query → same output across processes).

**Validation** — Every response passes through a validation agent that checks source attribution, price bounds, accounting identities, scale plausibility, and more. Strict mode rejects data that fails validation.

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
        SEC / Yahoo /    Template-based    Rule engine +
        FRED adapters    + optional LLM    scale / sign /
        + cache layer                     identity checks
```

### Source Roles

| Source | Primary For | Role Elsewhere |
|--------|-------------|---------------|
| **SEC EDGAR** | Financial statements, filings, company facts (XBRL) | — |
| **Yahoo Finance** | Market data (quotes, history) | Verification / fallback for financials |
| **FRED** | Macroeconomic indicators | — |

---

## CLI Commands

Every command listed here works. No decorative flags.

```bash
# Natural language query (routes automatically)
zion query "Get AAPL stock price"
zion query "Show me Tesla financials" --format json

# Direct commands
zion quote AAPL
zion history AAPL --period 6mo --interval 1wk
zion financials AAPL --statement income --quarterly --source sec
zion filings AAPL --form 10-K --limit 5
zion macro GDP --start 2020-01-01
zion info AAPL

# SEC-specific
zion filing-markdown AAPL --form 10-K    # experimental
zion company-facts AAPL

# Synthetic data
zion synthesis

# Global options
zion --strict ...     # fail on validation errors
zion --verbose ...    # debug logging
zion --version
```

### Output Formats

All commands support `--format markdown` (default), `--format json`, and `--format csv`.

---

## Project Structure

```
zion-terminal/
├── src/zion_terminal/
│   ├── orchestrator/         # Request routing, intent parsing
│   ├── agents/
│   │   ├── retrieval/        # Data retrieval + source adapters (SEC, Yahoo, FRED)
│   │   ├── synthesis/        # Synthetic entity generation
│   │   └── validation/       # Correctness checks (scale, sign, identity, bounds)
│   ├── pipeline/             # Filing HTML→markdown→segmentation→verification
│   ├── verification/         # XBRL fact mapping, markdown extraction, reconciliation
│   ├── cache/                # File-based caching with TTL (diskcache)
│   ├── models/               # Pydantic data models, source roles
│   ├── outputs/              # Formatters (markdown, JSON, CSV)
│   ├── providers/            # LLM provider abstraction (none/openai/ollama)
│   ├── config/               # Settings from env / .env
│   └── cli.py                # Click-based CLI
├── tests/
│   ├── unit/                 # 300+ unit tests
│   ├── integration/          # End-to-end tests (mocked)
│   ├── benchmarks/           # Converter and parser benchmarks
│   └── fixtures/             # HTML filing fixtures, query corpus
├── scripts/
│   └── run_benchmarks.py     # Real benchmark runner
├── docs/
│   ├── context/              # Architecture notes, design decisions
│   ├── decisions/            # ADRs (numbered)
│   ├── benchmarks/           # Latest benchmark results (JSON + markdown)
│   ├── schemas/              # Response schemas
│   ├── runbooks/             # How-to guides
│   └── status/               # Changelog, implementation status
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Getting Started

**Prerequisites:** Python 3.11+

```bash
git clone https://github.com/Scott-Switzer/zion-terminal.git
cd zion-terminal
pip install -e ".[dev]"
cp .env.example .env
```

Add your API keys to `.env`:
- `FRED_API_KEY` — free from [FRED](https://fred.stlouisfed.org/docs/api/api_key.html) (required for macro data)
- `EDGAR_IDENTITY` — your name and email per SEC policy (required for SEC data)
- `LLM_PROVIDER` — `none` (default), `openai`, or `ollama` (LLM is optional)

**Core retrieval works without any LLM.** The LLM is only used for ambiguous query parsing (fallback) and synthetic press release generation (optional).

---

## Key Design Decisions

1. **SEC-first for financial statements.** NL queries and direct API calls both route financials to SEC EDGAR. Yahoo is a fallback, never silent.
2. **Single filing pipeline.** All SEC filing conversion goes through `pipeline/filing_pipeline.py`. No parallel conversion paths.
3. **Deterministic synthesis.** Same query produces same output across processes (uses `hashlib.sha256`, not `hash()`).
4. **Strict mode.** `--strict` makes validation failures hard errors. Without it, warnings are surfaced but data is still returned.
5. **No LLM required.** Core data retrieval, validation, and synthesis all work with `LLM_PROVIDER=none`.

See `docs/decisions/` for full ADR history.

---

## Testing

```bash
# All tests
python -m pytest tests/

# Without benchmarks (faster)
python -m pytest tests/ -p no:benchmark

# Specific area
python -m pytest tests/unit/test_regression_fixes.py -v
```

Current: **413 tests passing, 1 skipped** (v0.8.2).

---

## Benchmarks

Run `python scripts/run_benchmarks.py` to generate real benchmark data.

Latest results (from actual measurements, not estimates):

| Metric | Value |
|--------|-------|
| Tests | 400 passing, 1 skipped |
| Reconciliation fixtures | 2 tested, 2 passed (company-facts, no Arelle needed) |
| HTML fixtures | 5 (all passing, live verification) |
| Parser corpus | 109 queries, 100% accuracy |
| Converter engine | BeautifulSoup DOM (with regex fallback) |
| Arelle/XBRL | Optional dependency, graceful degradation |

See `docs/benchmarks/latest.md` for full results.

---

## Known Limitations

- **SEC financials** depend on `edgartools` parsing quality. Some filings may not parse cleanly.
- **Yahoo Finance** is an unofficial API and may break.
- **Arelle** is not installed by default. Install with `pip install zion-terminal[xbrl]`.
- **Company facts** pagination works but namespace filtering depends on edgartools DataFrame column names.
- **Cross-source reconciliation** is structural only — no live SEC-vs-Yahoo comparison yet.
- **Filing-markdown** is experimental. Wrapper filings may produce incomplete results.
- **Historical retrieval** depends on SEC submissions endpoint, which returns ~1000 most recent filings. Very old filings may not be available.
- **Verification status** is `structural_only` in most environments (XBRL and cross-source require additional setup).

---

## Roadmap

### Phase 1: Data Retrieval (current)
- [x] SEC EDGAR adapter (filings, financials, company facts, filing→markdown)
- [x] Yahoo Finance adapter (quotes, history, financials, company info)
- [x] FRED adapter (macro indicators)
- [x] Intent parser (rule-based, LLM fallback)
- [x] Orchestrator with public API methods
- [x] CLI (10 commands)
- [x] Output formatting (markdown, JSON, CSV)
- [x] File-based caching with TTL
- [x] Validation agent with scale/sign/identity checks
- [x] Filing pipeline (HTML→markdown→segments→verification)
- [x] Deterministic synthesis
- [ ] Cross-source reconciliation (SEC vs Yahoo)
- [ ] XBRL live validation (requires Arelle)

### Phase 2: Synthetic Entities (planned)
- [ ] Full-text SEC filing generation (10-K, 10-Q, 8-K)
- [ ] Press releases and analyst notes (requires LLM)
- [ ] End-to-end entity generation pipeline

---

## License

[MIT](LICENSE)
