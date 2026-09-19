# Zion Tool Contract V2 Operations

## Canonical surfaces

- REST/MCP staging Worker: `zion-financial-serving-v2-thin-staging`
- Canonical REST domain: `https://api-thin-staging.scotttunnel.xyz`
- MCP endpoint: `https://api-thin-staging.scotttunnel.xyz/mcp`
- Workers diagnostic endpoint: `https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev`
- Serving release: `895eb69371fa2e944eed20463f7f9fc2`
- Temporal contract: `financial-temporal-v1`, SHA256 `5d225691cb60251d1997bb8d749267da845f0b3cda32137cbf76c3fbd2783b1d`
- Tool contract: `zion-tool-contract-v2`, SHA256 `763e01d48975d02532224de73d668952bc33a199021654b5653b6c6bff9235f8`

`api-staging.scotttunnel.xyz` remains the full compatibility surface. Do not repoint it to the thin Worker.

## Reproducible deployment

Deploy only from a clean merged `main` checkout. The deployment workflow passes build identity as Wrangler variables; it does not use dashboard-edited source:

```bash
cd deploy/cloudflare-query
npx wrangler@4.135.0 deploy --config thin/wrangler.jsonc \
  --var "GIT_SHA:${GIT_SHA}" \
  --var "TOOL_CONTRACT_VERSION:zion-tool-contract-v2" \
  --var "TOOL_CONTRACT_SHA256:763e01d48975d02532224de73d668952bc33a199021654b5653b6c6bff9235f8" \
  --var "SERVICE_VERSION:zion-tool-contract-v2-${GIT_SHA}"
```

Before deployment, regenerate the manifest and require no diff:

```bash
python scripts/generate_tool_contract.py
git diff --exit-code -- contracts/zion-tool-contract-v2.json contracts/zion-tool-contract-v2.sha256
```

The Worker must expose `git_sha`, service version, contract hash, temporal hash, and serving release in `/v2/capabilities`.

## Acceptance

Hosted acceptance runs from a GitHub runner, not the developer Mac. It checks DNS, TLS hostname validation, `/healthz`, `/readyz`, both capability documents, all 11 REST calls, MCP initialize/tools/list/tools/call, release identity, and contract identity. Runtime telemetry is diagnostic only and is excluded from semantic parity comparisons.

The official conformance CLI currently installed by the workflow is queried at runtime. Its current package may label the latest available dated protocol as `2025-11-25`; the workflow records the package version and scenario list rather than claiming an unavailable `2026-07-28` package. Financial MCP canaries additionally use the official SDK client exercised by the conformance runner.

The generic official tool-call scenarios use an `add_numbers` fixture tool and are not applicable to this production financial registry, which is intentionally frozen at exactly 11 tools. Applicable MCP protocol scenarios are run explicitly; no expected-failures file is used.

## Scheduled and nightly checks

`Serving V2 Custom Domain Smoke` remains the lightweight scheduled availability check. It must verify expected release, Temporal hash, Tool Contract hash, capabilities, and a representative REST and MCP call.

`Tool Contract V2 Nightly Acceptance` is the heavier manual/nightly workflow. It runs the applicable official MCP protocol scenarios, 11-tool REST/MCP canaries, parity, PIT checks, and three-run performance samples.

## Rollback

Cloudflare deployment history is immutable. Record the prior and new version IDs from Wrangler. Roll back the Worker version using the Cloudflare deployment rollback command documented by the installed Wrangler version, or redeploy the prior merged `main` SHA with the same configuration. Never alter the financial CURRENT pointer or an immutable serving release during a contract rollback.

After rollback, rerun `/healthz`, `/v2/capabilities`, the representative REST/MCP canaries, and verify the expected serving release remains `895eb69371fa2e944eed20463f7f9fc2`.

## Diagnosis

- `CONTRACT_MISMATCH`: compare runtime contract hash to the sidecar and capability set; do not run a financial deployment with drift.
- Stale Worker: compare `/v2/capabilities.git_sha` and deployment version to the merged source SHA.
- Custom-domain drift: inspect the Worker Custom Domain owner and compare it with the thin Worker; do not change `api-staging.scotttunnel.xyz`.
- Local TLS failure: compare with the GitHub-hosted TLS check. If hosted TLS passes and the Mac fails, classify it as `LOCAL_TRANSPORT_ENVIRONMENT_ISSUE`.
- MCP conformance failure: run the exact failing scenario against the Workers endpoint, inspect protocol version negotiation and notification status codes, then rerun without expected-failures suppression.
- Serving release mismatch: stop deployment acceptance; verify `gold/serving/coverage25/CURRENT.json` and do not mutate data.

No credentials, R2 secrets, full financial payloads, or tracebacks belong in workflow logs.
