# Financial Truth Query V1

Zion exposes a deterministic HTTP boundary for real and synthetic financial worlds.

## Endpoints

- `GET /healthz` — cheap process health check.
- `GET /readyz` — process readiness response.
- `GET /v1/capabilities` — schema, service, world, and metric capabilities.
- `POST /v1/query` — bounded natural-language query grammar for `revenue`, `operating_margin`, and `last_price`.

The query service does not require an LLM. It parses the bounded request, routes by `world.world_type`, reads published immutable artifacts, filters evidence by `as_of`, and assembles the common response contract.

## Staging deployment

The verified staging endpoint is:

```text
https://zion-financial-query-staging.scswitzer.workers.dev
```

It is a Cloudflare Python Worker using FastAPI through the Workers ASGI adapter. It is isolated from production custom domains.

## Release boundaries

The staging real path resolves PPE `control/market-terminal/CURRENT.json` once per request, reads the pinned AAPL market release, and reads the corresponding AAPL SEC filing and filing manifest from the published PPE SEC corpus. Evidence identifies the release prefix, SEC artifact, producer, and filing availability date.

The staging synthetic path resolves `control/synthetic-worlds/test-world-001/CURRENT.json`, requires `qc_status: PASS`, and reads only `manifest.json`, `qc_certification.json`, and files below `public/`. The certified serving release is published under an immutable digest prefix. Hidden world state is not uploaded.

No query path calls a live financial provider, reads Market Fuzzer hidden state, or falls back to a fixture when a native release is unavailable.

## Errors and safety

Errors use a stable `{error: {request_id, code, message, retryable}}` envelope. Query size is bounded, request IDs are validated/generated, `as_of` excludes evidence first available after the requested time, and configured artifact key construction rejects traversal components and unexpected separators.

## Local verification

```bash
.venv/bin/python -m pytest -q
```

The full Zion suite passes with the API tests included. The local endpoint can be served with:

```bash
uvicorn zion_terminal.api:create_app --factory --host 127.0.0.1 --port 8787
```

See `CLOUDFLARE_DEPLOYMENT.md` for publication, deployment, smoke, and rollback procedures.
