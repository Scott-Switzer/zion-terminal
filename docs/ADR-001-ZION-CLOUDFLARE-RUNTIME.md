# ADR-001: Zion Cloudflare runtime

## Decision

Use a Cloudflare Python Worker for the isolated staging query runtime.

## Evidence

- The current Wrangler 4.132.0 toolchain accepted `compatibility_flags: ["python_workers"]` and deployed FastAPI through `workers.asgi`.
- The first probe failed because FastAPI was not vendored; running the current `pywrangler sync` with uv 0.12.15 produced the required Pyodide vendor set.
- The deployed Worker successfully served `/healthz`, `/readyz`, `/v1/capabilities`, and both producer-backed query paths.
- The final compressed upload was approximately 4.36 MiB with a 2.16 s reported startup time.

## Alternatives

A Zion Container was not selected because the query path is compatible with Python Workers and the Worker avoids a separate container lifecycle. The existing PPE SEC container remains unrelated and was not reused.

## Bindings

The staging Worker has read-only R2 bindings to the existing `financial-system-datasets` and `ppe-sec-intelligence-prod` buckets. No API keys or provider credentials are embedded.

## Tradeoffs

The deployment shim is intentionally small and only vendors FastAPI/Pydantic and the Workers runtime. It reads bounded immutable PPE release objects and certified synthetic public-release objects. It does not import Zion's research-provider dependency graph.

## Rollback

Staging rollback is version-based:

```bash
wrangler deployments list --name zion-financial-query-staging
wrangler rollback --name zion-financial-query-staging --version-id <previous-version-id>
```

The currently verified deployment version is recorded in the handoff, and the `workers.dev` route is isolated from production domains.
