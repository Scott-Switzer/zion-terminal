# Financial Truth Query and Tools V1

Zion exposes a deterministic HTTP boundary for real and synthetic financial worlds. It does not require an LLM, call live providers, or fall back to fixtures.

## Endpoints

- `GET /healthz` — cheap process health check.
- `GET /readyz` — process readiness response.
- `GET /v1/capabilities` — schema, service, world, metric, tool, and calculation capabilities.
- `POST /v1/query` — bounded natural-language facade.
- `POST /v1/tools/{tool_name}` — direct access to the shared deterministic registry.

Supported tools are `resolve_entity`, `get_price`, `get_price_history`, `get_fundamentals`, `get_filing`, `get_evidence`, `calculate`, and `compare`. See `FINANCIAL_TOOLS.md` for schemas and bounds.

## Query grammar

Supported examples include:

```text
Give me revenue, operating margin and last price for AAPL
Show AAPL price history for the last 30 days
What is AAPL gross margin?
Compare AAPL and MSFT operating margins over the last 4 quarters
Show the latest 3 AAPL 10-Q filings
```

The parser is deliberately bounded. Unsupported requests return `QUERY_NOT_SUPPORTED`; it never invents a financial answer.

## Staging deployment

```text
https://zion-financial-query-staging.scswitzer.workers.dev
Worker version: da70d13b-9e8d-4cfb-8a96-33f101596df8
```

This is a Cloudflare Python Worker using FastAPI through the Workers ASGI adapter and is isolated from production custom domains.

## Release boundaries

Real requests resolve PPE `control/market-terminal/CURRENT.json` and the derived fundamentals pointer once per request. Fundamentals are served from immutable per-symbol JSON read models under `gold/market-terminal/fundamentals/releases/<digest>/`, derived offline from published PPE SEC artifacts. Evidence retains period, filing date, accession, availability, source context, and release provenance.

Synthetic requests resolve the certified Market Fuzzer pointer, require QC `PASS`, and read only `manifest.json` and `public/*`. Hidden world state is never exposed.

## Semantics and safety

Errors use `{error: {request_id, code, message, retryable}}`. Request body size is bounded at 32 KiB, query text at 2,000 characters, price history at 500 rows, fundamentals at 40 periods, filings at 20 records, and compare at 10 entities. `as_of` excludes observations whose `available_at` is later than the requested timestamp. Annual and quarterly periods are never silently substituted.

## Local verification

```bash
.venv/bin/python -m pytest -q
python3 -m py_compile deploy/cloudflare-query/src/worker.py deploy/cloudflare-query/build_read_models.py
```

See `CLOUDFLARE_DEPLOYMENT.md`, `FINANCIAL_TOOLS.md`, and `FUNDAMENTAL_METRICS.md` for publication, deployment, rollback, and semantic details.
