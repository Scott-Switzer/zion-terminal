# Compact valuation service

`deploy/valuation` is a small private Cloudflare Python Worker. It reuses
`financial-system-datasets`, reading only the public SEC valuation index and the
referenced 128-KiB-bounded, SHA-256-verified company object. It neither changes
shared pointers nor reads the private research bucket. Existing Zion query,
entity-action, market and research deployments are unaffected.

The DCF app binds `ZION_VALUATIONS` to `zion-valuation` and requests
`GET /v1/valuation/company/MSFT?as_of=YYYY-MM-DD`. Packet contract is
`ppe-valuation-packet-v1`, produced by DCF's `scripts/publish_ppe_packets.py`.
The app validates field units/periods/availability and preserves explicit fallback
sources. Source facts are not forecasts. A configured binding is not coverage proof.

Missing ticker: 404. Invalid query: 400. Historical cutoff before publication:
422. Integrity/storage failure: 503. Public workers.dev and preview URLs are
disabled; only service-binding callers can access the deployment. No bearer tokens.

Cache: current index 60 seconds; immutable objects one year, with SHA-256 checked
on every use. Cache failures fall back to R2. Each request pins one index and packet.

From `deploy/valuation`, use the project's supported UV/pywrangler runtime:
`uv run pywrangler deploy --dry-run`, then `uv run pywrangler deploy`.
Run `pytest tests/unit/test_valuation_packets.py` from the repository root.
Deploy this private service before adding its binding to the DCF release.
To roll back the consumer, remove its binding or deploy the prior DCF version;
its existing R2 reader remains available. Data publications preserve earlier
immutable packets and conditionally promote only the valuation index.
