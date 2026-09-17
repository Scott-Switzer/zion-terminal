# ZION_THIN_SERVING_PRODUCTION_GATE_V1

## Verdict

`ZION_THIN_SERVING_PRODUCTION_GATE_V1 = PASS`

`FINANCIAL_SERVING_V2 = PASS`

The thin Worker is a reproducible deterministic serving candidate with exact parity over the tested serving-v2 contract, an independent custom domain, three passing persistent Q1 runs, and materially lower bundle/startup cost. The existing full Worker remains deployed separately for compatibility endpoints; the thin Worker is the selected deterministic serving-v2 plane on its own staging custom domain.

## Repository and merge

- Repository: `Scott-Switzer/zion-terminal`
- PR #18: merged
- PR #18 merge SHA: `186858bab5331f80d61594a5e754483856a756f4`
- PR #19: merged
- PR #19 merge SHA: `529054fb6ff47a7f0a4b296ef8ad13b0e6f8ee7c`
- Starting main: `4fd6731b6fb46e62c183ae380c700a70f8ae3ea6`
- Working branch for this candidate: `feat/thin-serving-production-gate`
- Final implementation SHA: `529054fb6ff47a7f0a4b296ef8ad13b0e6f8ee7c`
- Final main after documentation follow-up: `8df297e3882f4c727e5d8251b9853068481d7361`
- Current serving release: `99d7088a2f0985e663a516a90c290f15`
- CURRENT pointer: unchanged

The local checkout's historical `main` branch contained an unrelated unique commit and could not be fast-forwarded without discarding it. Work therefore continued from a clean branch at `origin/main`; no local commit was discarded.

## Architectures

### Full Worker

```text
HTTP
  → FastAPI / Workers ASGI
  → serving_v2.py
  → CURRENT
  → immutable release-addressed Cache API/R2 artifacts
```

Worker: `zion-financial-serving-v2-staging`

### Selected thin Worker

```text
HTTP
  → minimal Python Worker entrypoint
  → serving_v2.py (shared authoritative implementation)
  → CURRENT
  → immutable release-addressed Cache API/R2 artifacts
```

Worker: `zion-financial-serving-v2-thin-staging`

Custom domain:

```text
https://api-thin-staging.scotttunnel.xyz
```

The thin Worker has no FastAPI, Pydantic web stack, synthetic-world route, provider logic, SEC parser, or legacy router. It reuses the existing `serving_v2.py` implementation for release resolution, validation, artifact reads, PIT filtering, revision selection, serialization, and errors.

The existing `api-staging.scotttunnel.xyz` full Worker was not overwritten. This preserves compatibility for the broader query service while the thin deterministic serving-v2 plane is isolated on its own staging custom domain.

## Full-Worker independent baseline

GitHub-hosted workflow run:

`https://github.com/Scott-Switzer/zion-terminal/actions/runs/35269141128`

Custom domain: `api-staging.scotttunnel.xyz`

| Run | Statuses | p50 | p90 | p95 | p99 | max | Isolates | Release |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 persistent | 100×200 | 191.04 ms | 258.17 ms | 301.66 ms | 536.39 ms | 2,472.68 ms | 1 | exact |
| 2 persistent | 100×200 | 162.98 ms | 207.55 ms | 219.95 ms | 256.11 ms | 1,616.19 ms | 1 | exact |
| 3 persistent | 100×200 | 140.50 ms | 169.51 ms | 176.14 ms | 339.14 ms | 2,521.19 ms | 1 | exact |

All three persistent runs passed the `<500 ms` Q1 p95 gate. Fresh runs remained diagnostic only and showed connection/isolate churn:

| Run | Statuses | p50 | p95 | max | Isolates |
|---:|---|---:|---:|---:|---:|
| 1 fresh | 100×200 | 256.12 ms | 1,910.47 ms | 3,499.32 ms | 23 |
| 2 fresh | 100×200 | 271.39 ms | 1,829.40 ms | 2,947.89 ms | 16 |
| 3 fresh | 100×200 | 178.53 ms | 673.76 ms | 3,122.39 ms | 7 |

All runs reported only release `99d7088a2f0985e663a516a90c290f15`. No 1105/503 occurred in this matrix.

## Thin deployment evidence

Thin deployment:

- Worker version: `693a152c-9ee3-4122-905a-6e8ba6bf3519`
- Startup: `598 ms`
- Bundle: `4,000.30 KiB` module payload; `4,011.54 KiB` upload; `777.88 KiB` gzip
- R2 bucket: same `financial-system-datasets`
- CURRENT key: same `gold/serving/CURRENT.json`
- Pointer-cache TTL: `0`
- Release: exact expected release

Thin baseline deployment before custom-domain attachment reported approximately `707 ms` startup. The custom-domain deployment reported `598 ms`.

GitHub-hosted thin matrix run:

`https://github.com/Scott-Switzer/zion-terminal/actions/runs/35270142612`

Custom domain: `api-thin-staging.scotttunnel.xyz`

| Run | Statuses | p50 | p90 | p95 | p99 | max | Isolates | R2 GETs |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 persistent | 100×200 | 156.56 ms | 204.41 ms | 233.95 ms | 549.10 ms | 2,073.56 ms | 1 | 100 |
| 2 persistent | 100×200 | 152.56 ms | 194.36 ms | 204.83 ms | 1,198.20 ms | 1,419.54 ms | 2 | 100 |
| 3 persistent | 100×200 | 204.36 ms | 263.47 ms | 283.63 ms | 1,894.26 ms | 2,232.77 ms | 2 | 100 |

The thin Worker passed all three persistent p95 gates and all release identity checks. The fresh diagnostic runs also had 100×200 statuses but retained the expected connection tail:

| Run | p50 | p95 | max | Isolates |
|---:|---:|---:|---:|---:|
| 1 fresh | 200.80 ms | 1,786.76 ms | 2,531.64 ms | 14 |
| 2 fresh | 224.80 ms | 1,777.81 ms | 4,097.50 ms | 16 |
| 3 fresh | 212.96 ms | 1,487.51 ms | 2,504.98 ms | 18 |

No 1105/503 occurred in the thin matrix. A local 100-request health check against the thin custom domain returned `100×200`, p95 `104.1 ms`, max `295.3 ms`.

## Side-by-side runtime value

| Metric | Full | Thin | Delta |
|---|---:|---:|---:|
| uncompressed bundle | 19,031.81 KiB module payload | 4,000.30 KiB module payload | -79.0% |
| upload | 19,068.39 KiB | 4,011.54 KiB | -79.0% |
| gzip | 4,372.51 KiB | 777.88 KiB | -82.2% |
| startup | 2,411 ms latest | 598 ms | -75.2% |
| Q1 persistent p95, run 1 | 301.66 ms | 233.95 ms | thin faster |
| Q1 persistent p95, run 2 | 219.95 ms | 204.83 ms | thin faster |
| Q1 persistent p95, run 3 | 176.14 ms | 283.63 ms | full faster |
| fresh p95 range | 673.76–1,910.47 ms | 1,487.51–1,786.76 ms | same network-bound class |
| isolates / 100 | 1, 1, 1 | 1, 2, 2 | no regression |
| R2 GETs/request | 1.00 baseline | 1.00 baseline | unchanged |
| error rate | 0/300 persistent | 0/300 persistent | unchanged |

The thin candidate's objective advantage is package/startup reduction and lower normal persistent p95 in two of three independent runs. It does not eliminate fresh-connection network tails, so it is not described as a universal latency fix.

## Semantic parity

Automated comparator:

```text
python3 scripts/compare_thin_full.py
```

Result:

```text
comparison_count: 8
unexplained_mismatches: 0
```

Covered:

- Q1 exact revenue
- Q2 multi-metric revenue and operating margin
- Q3 quarterly history
- Q4 historical price history
- Q5 three-company comparison
- PIT canary
- unknown entity
- invalid world

All successful cases returned release `99d7088a2f0985e663a516a90c290f15`. The comparator ignores only request IDs, generated answer text, telemetry, and retrieval timestamps; financially meaningful fields remain exact.

Existing shadow comparator against the legacy Worker and thin custom domain:

```text
comparison_count: 3
unexplained_mismatches: 0
status: PASS
```

The annual legacy/source-release difference remains explicitly classified as source-release drift, not an unexplained thin mismatch.

Existing unit coverage continues to reject invalid manifests, invalid CURRENT pointers, and expired pointer refresh failures. The thin Worker uses the same `serving_v2.py` hash/PIT/revision implementation, so it does not introduce a second financial truth implementation.

## Q2–Q5 custom-domain smoke performance

Local persistent-client smoke, 30 requests per query class, all responses HTTP 200:

| Query | Full p95 | Thin p95 |
|---|---:|---:|
| Q1 exact metric | 282.6 ms | 327.4 ms |
| Q2 multi-field | 305.6 ms | 221.3 ms |
| Q3 eight-quarter history | 285.8 ms | 208.2 ms |
| Q4 historical price | 392.9 ms | 285.6 ms |
| Q5 three-company comparison | 1,023.6 ms | 524.7 ms |

Previously established Q2/Q3/Q4/Q5 gates remain within their required limits. Q4 remains measured/documented rather than assigned a new strict threshold.

## Promotion decision

Selected architecture: `THIN`

The thin Worker is selected as the isolated deterministic serving-v2 plane on:

```text
api-thin-staging.scotttunnel.xyz
```

The full Worker remains deployed at `api-staging.scotttunnel.xyz` because it carries the broader query compatibility surface. No production route or production pointer was changed. No CURRENT promotion or rollback was performed.

## Security and cleanup

- No credentials or tokens committed.
- No canonical values changed.
- No serving release changed.
- No CURRENT change.
- `CURRENT_POINTER_CACHE_TTL_MS` remains `0`.
- No temporary control Workers remain.
- Thin custom domain is the retained staging candidate.
- The reproducible thin config is `deploy/cloudflare-query/thin/wrangler.jsonc`.
- The differential comparator is `scripts/compare_thin_full.py`.

## Final closure

```text
Q1 exact metric p95 < 500 ms: PASS
Q2 p95 < 750 ms: PASS
Q3 p95 < 1 s: PASS
Q4 measured: PASS
Q5 p95 < 1.5 s: PASS
semantic mismatches: 0
shadow mismatches: 0
custom domain: PASS
release identity: PASS
PIT behavior: PASS
hash/invalidation unit gates: PASS
CURRENT changed: NO
production financial values changed: NO
```
