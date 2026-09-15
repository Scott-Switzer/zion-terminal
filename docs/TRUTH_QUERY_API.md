# Financial Truth Query V1

Zion exposes a deterministic HTTP boundary for real and synthetic financial worlds.

## Endpoints

- `GET /healthz` — cheap process health check.
- `GET /readyz` — process readiness response.
- `GET /v1/capabilities` — schema, service, world, and metric capabilities.
- `POST /v1/query` — bounded natural-language query grammar for `revenue`, `operating_margin`, and `last_price`.

The query service does not require an LLM. It parses the bounded request, routes by `world.world_type`, reads a pinned serialized release, filters evidence by `as_of`, and assembles the common response contract.

## Release boundaries

The real client reads one PPE-derived serialized release snapshot per request. The snapshot contains the published release identity and evidence provenance; it must be materialized from PPE's sealed release path by the integration runner or deployment client. The synthetic client reads only `manifest.json` and files below `public/`, and requires a separate QC report with `status: PASS` unless an explicitly non-public development override is used.

No query path reads Market Fuzzer hidden state, calls a live financial provider, or falls back to a fixture when a native release is unavailable.

## Errors and safety

Errors use a stable `{error: {request_id, code, message, retryable}}` envelope. Query size is bounded, request IDs are validated/generated, `as_of` excludes evidence first available after the requested time, and public artifact path resolution rejects traversal and symlink escape.

## Local verification

```bash
.venv/bin/python -m pytest -q
```

The current local suite passes with the API tests included. The endpoint can be served with the existing optional API dependencies using:

```bash
uvicorn zion_terminal.api:create_app --factory --host 127.0.0.1 --port 8787
```

No staging deployment is claimed: Zion has no checked-in Wrangler/container deployment configuration, and no safe staging route was created in this milestone. The next deployment step is to add a bounded staging runtime that materializes the two producer release clients without exposing R2 or hidden artifacts.
