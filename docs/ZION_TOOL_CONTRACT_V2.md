# Zion Tool Contract V2

Contract version: `zion-tool-contract-v2`

The canonical registry is `deploy/cloudflare-query/src/contract_v2.py`. Its canonical JSON bytes are sorted and compact and are fingerprinted with SHA-256 at runtime. REST V2 capabilities and MCP `tools/list` are generated from that registry; handlers do not maintain a second tool list.

## Transport

- REST: `POST /v2/tools/<tool>` with `{world, arguments, as_of?, serving_release_id?}`.
- Discovery: `GET /v2/capabilities`.
- MCP adapter: `POST /mcp`, using JSON-RPC initialize, `tools/list`, and `tools/call` over stateless HTTP.
- V1 routes remain compatibility routes and are not the V2 schema source.

## Tools

`resolve_entity`, `resolve_security`, `get_fundamentals`, `get_price`, `get_price_history`, `get_corporate_actions`, `get_revision_history`, `get_evidence`, `compare`, `calculate`, and `screen` are read-only and deterministic. Every registry entry contains its input schema, description, supported worlds, and annotations. The current release supports the real `us-public-markets` world; calculator execution also supports a synthetic world envelope for future compatibility.

Every successful response uses `zion-tool-response-v2` and contains `schema_version`, `tool_contract_version`, `request_id`, `tool`, `world`, `data`, `evidence`, `quality`, `provenance`, and a common `release` block. Release metadata includes the serving release, temporal contract, contract hash, and source snapshot when available.

## Bounds and semantics

Requests are limited to 32 KiB. Fundamental metrics are limited to 20 and lookback to 40; comparisons are limited to 10 entities and 40 observations; price history is limited to 500 rows; screens have at most 8 filters, 3 sort terms, and 100 results. All `as_of` filtering delegates to the frozen `financial-temporal-v1` semantics. No future revisions or corporate actions are exposed.

`calculate` supports only `difference`, `percent_change`, `average`, `sum`, and `ratio`, and uses `Decimal` from string inputs. It never evaluates expressions, SQL, or Python. Revision history reads the immutable per-entity fundamentals artifacts and applies the same availability cutoff. Screen evaluates bounded filters over the release resolver index and returns evidence references for included values; it is deliberately bounded and does not expose arbitrary scans.

## Errors

Errors use `{error: {request_id, code, message, retryable}}`. Invalid JSON/arguments map to 400, unavailable resources to 404 or 503, semantic unsupported operations to 422, and unexpected serving failures to 503. Internal traces, R2 paths, and configuration are never exposed.

## Compatibility and freezing

V1 remains available during migration. V2 is frozen as a compatibility contract once its external acceptance suite passes. Optional `serving_release_id` is accepted only when it equals the immutable CURRENT release; arbitrary R2 keys and non-CURRENT releases are rejected. Breaking changes require `zion-tool-contract-v3`; additive optional fields may be added only with a documented non-breaking change and a changed contract fingerprint. Runtime telemetry is transport metadata and is not part of semantic parity comparisons.
