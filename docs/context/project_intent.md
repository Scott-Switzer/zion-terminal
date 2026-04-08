# Project Intent

## What This Project Is

Zion Terminal is building toward an **agentic, open-source alternative to the Bloomberg Terminal** for institutional financial data access and analysis.

## Two Components

### Component 1 — Unified Data Retrieval (Phase 1, current focus)
A single natural-language query interface that pulls and caches financial data from public sources. SEC EDGAR is the primary source for company financial data. Yahoo Finance provides market data convenience and cross-checking.

### Component 2 — Synthetic Entity Engine (Phase 2, planned)
Generates semi-fictional companies with internally consistent financial statements, 10-K-style filings, and supporting documents for backtesting, training AI agents, and research.

## Design Philosophy

1. **One agent, one job** — each agent handles one task. More reliable, testable, replaceable.
2. **Never full-document context** — use RAG + targeted retrieval. Never load a full 10-K into a model's context window.
3. **Stopping points at every handoff** — if output doesn't meet the schema, it goes back, not forward.
4. **Expose all assumptions** — every financial calculation shows its definition and allows user override.
5. **90% accuracy is the target** — better than manual, cheaper than Bloomberg. Perfect is the enemy of shipped.
6. **On-prem first for European clients** — deployable with client-provided API keys, zero data egress.
7. **Bloomberg is ground truth, not training data** — SEC/XBRL is the primary validation source.
8. **Pre-process once, query cheaply forever** — heavy processing at ingestion, cheap targeted retrieval thereafter.

## Source of Truth
- SEC EDGAR: primary for financial statements, filings, company facts
- Yahoo Finance: primary for market data (quotes, history), verification for financials
- FRED: primary for macroeconomic data
- Arelle: XBRL validation and fact extraction
- Bloomberg: internal ground truth (never published or trained on)

## Team Context
- Prof. Frenzel: Project Lead / Architect
- Scott Switzer: Developer (Bloomberg access at Chapman)
- Kavin Ravi: Developer (benchmarking, format optimization)
- Dylan Massaro: TA / Developer (EDGAR pipeline, validation)
