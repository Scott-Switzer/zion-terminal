# Zion Tool Contract V2 Operations

## Canonical surfaces

- REST/MCP staging Worker: `zion-financial-serving-v2-thin-staging`
- Canonical REST domain: `https://api-thin-staging.scotttunnel.xyz`
- MCP endpoint: `https://api-thin-staging.scotttunnel.xyz/mcp`
- Workers diagnostic endpoint: `https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev`
- Serving release: `6681f0f5437eae68861d6739b1a9438a`
- Screen artifact: `gold/serving/releases/6681f0f5437eae68861d6739b1a9438a/screens/latest.json` (27 rows, SHA-256 `53569bed1863f0f6eaa2df31786814b6b190f72404b82d3410e7dda3f992e482`)
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

The frozen conformance requirements are pinned to upstream conformance commit `232a9014457eaf4c59559916e3d616d2f7f28f05`; requirement hashes are `2025-11-25=f33a304dfa2cbd999c24026a3453a64f377bba0c8aa80addadaf05862d212371` and `2026-07-28=ae2f4f6210fd729e2e318edd5bbfa31a43cee0bc608e48052fa26dbf1d939b57`. The 2025-11-25 path uses the stateful lifecycle, while 2026-07-28 uses stateless per-request `_meta` and standard routing headers. The npm package is not used as a substitute for the frozen revision.

The production 2026 stateless transport scored 21/25 in `server-stateless`; the four remaining checks are explicitly untestable because they require fixture-only diagnostic tools (`test_missing_capability`, `test_streaming_elicitation`, and `test_logging_tool`) that must not be added to the frozen financial 11-tool registry. Production 11-tool canaries and REST/MCP parity are separate acceptance evidence. No expected-failures file is used.

## Scheduled and nightly checks

`Serving V2 Custom Domain Smoke` remains the lightweight scheduled availability check. It verifies expected release `6681f0f5437eae68861d6739b1a9438a`, Temporal hash, Tool Contract hash, capabilities, and representative REST/MCP calls. The hosted final run was `35481312801`; the full hosted operations run was `35480944367`.

`Tool Contract V2 Nightly Acceptance` is the heavier manual/nightly workflow. It runs the applicable official MCP protocol scenarios, 11-tool REST/MCP canaries, parity, PIT checks, and three-run performance samples.

## Rollback

Cloudflare deployment history is immutable. Record the prior and new version IDs from Wrangler. Roll back the Worker version using the Cloudflare deployment rollback command documented by the installed Wrangler version, or redeploy the prior merged `main` SHA with the same configuration. Never alter the financial CURRENT pointer or an immutable serving release during a contract rollback.

After rollback, rerun `/healthz`, `/v2/capabilities`, the representative REST/MCP canaries, and verify the expected serving release remains `6681f0f5437eae68861d6739b1a9438a`. The executed drill rolled Worker version `6060a84d-1d0a-4ca5-abba-07556349e4b1` back to `7fd2fac5-dc75-40ea-980a-e67e5741b3d8`, validated health/capabilities, then rolled forward to `1b426660-a229-4615-a468-0d4bfbc4946b` from merged main `119a7a15c27c78c104a550c26a6c730adb6ed3d1`.

## Diagnosis

- `CONTRACT_MISMATCH`: compare runtime contract hash to the sidecar and capability set; do not run a financial deployment with drift.
- Stale Worker: compare `/v2/capabilities.git_sha` and deployment version to the merged source SHA.
- Custom-domain drift: inspect the Worker Custom Domain owner and compare it with the thin Worker; do not change `api-staging.scotttunnel.xyz`.
- Local TLS failure: compare with the GitHub-hosted TLS check. If hosted TLS passes and the Mac fails, classify it as `LOCAL_TRANSPORT_ENVIRONMENT_ISSUE`.
- MCP conformance failure: run the exact failing scenario against the Workers endpoint, inspect protocol version negotiation and notification status codes, then rerun without expected-failures suppression.
- Serving release mismatch: stop deployment acceptance; verify `gold/serving/coverage25/CURRENT.json` and do not mutate data.

No credentials, R2 secrets, full financial payloads, or tracebacks belong in workflow logs.
