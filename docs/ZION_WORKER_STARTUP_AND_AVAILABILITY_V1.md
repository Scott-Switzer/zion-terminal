# ZION_WORKER_STARTUP_AND_AVAILABILITY_V1

## Verdict

`ZION_WORKER_STARTUP_AND_AVAILABILITY_V1 = PARTIAL`

The investigation identified a causal client/runtime boundary and added reusable diagnostic telemetry, but the primary three-run custom-domain SLA gate was not re-run from GitHub Actions after instrumentation and no production serving change is justified by the evidence yet.

## Starting state

- Repository: `Scott-Switzer/zion-terminal`
- Starting branch: `origin/main`
- Starting SHA: `4fd6731b6fb46e62c183ae380c700a70f8ae3ea6`
- Worker: `zion-financial-serving-v2-staging`
- Serving release: `99d7088a2f0985e663a516a90c290f15`
- `CURRENT_POINTER_CACHE_TTL_MS`: `0` (unchanged)
- Current pointer and canonical financial values were not changed.
- Existing focused tests: `15 passed` before edits.

## Hypothesis tree

The investigation separated: Python bootstrap, isolate churn, application execution, R2/cache work, TLS/connection setup, runner/network effects, Cloudflare POP behavior, and Python-runtime errors. The prior 1105 observations were not treated as proof of an application defect.

## Baseline bundle and deployment

A Wrangler dry run reported:

- total modules: `1,226`
- uncompressed upload: `19,066.94 KiB`
- gzip upload: `4,372.20 KiB`
- reported Worker Startup Time: approximately `2,270 ms` in the prior deployment
- current redeployment with diagnostic-only changes: `19,068.39 KiB`, `4,372.51 KiB`, `2,619 ms`

The vendored package composition is dominated by the Python web/runtime dependency graph:

- `pydantic_core`: about `4.3 MiB` in the source dependency tree
- `pydantic`: about `1.9 MiB`
- `fastapi`: about `0.9 MiB`
- `anyio`: about `0.6 MiB`
- `starlette`: about `0.3 MiB`
- source vendor tree: about `9 MiB`

The serving code itself is small (`serving_v2.py` about `22 KiB`, `worker.py` about `37 KiB`). No pandas, yfinance, SEC parser, LLM, or provider package is in the serving dependency tree.

## Diagnostic instrumentation

`DIAGNOSTIC_ISOLATE_TELEMETRY` is explicit and enabled only in the isolated `serving-v2` staging environment. The ID is initialized lazily on an isolate's first request, not at module/deploy snapshot construction. Diagnostic telemetry reports:

- `isolate_instance_id`
- `isolate_request_seq`
- `isolate_first_request_at`
- existing stage timing and R2/cache telemetry

The production/default environment keeps the diagnostic variable false. Financial semantics, hash validation, PIT filtering, release resolution, and artifact cache keys are unchanged.

## Connection and client decomposition

A local controlled run against the workers.dev URL used 100 requests per mode:

| endpoint | mode | statuses | isolates | p50 | p90 | p95 | p99 | max |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| full V2 | persistent | 200×100 | 1 | 232.6 ms | 314.4 ms | 374.8 ms | 480.7 ms | 486.9 ms |
| full V2 | fresh | 200×99 + 1 connection reset | 13 | 310.9 ms | 1,069.9 ms | 1,296.4 ms | 1,592.7 ms | 1,614.3 ms |
| thin V2 | persistent | 200×100 | 1 | 150.1 ms | 213.7 ms | 269.2 ms | 444.5 ms | 1,181.7 ms |
| thin V2 | fresh | 200×100 | 9 | 298.9 ms | 732.4 ms | 1,315.6 ms | 2,046.2 ms | 5,509.5 ms |

The persistent connection remained on one diagnostic isolate and had a much smaller tail. Fresh connections caused isolate churn and materially increased the tail even when all responses were HTTP 200. The fresh-connection result includes TLS/routing/connection setup and is not an application execution measurement.

The response telemetry for the slow fresh-connection samples had no usable application timing in the response because the delay occurred before the request reached the application. This is direct evidence that the client/connection path, not only Worker application code, contributes to the 1–3 second outliers.

## Controls

A minimal JavaScript Worker control deployed successfully:

- upload: `0.29 KiB` gzip `0.22 KiB`
- startup: `9 ms`
- 50 persistent health checks: p95 `27.0 ms`
- 50 fresh health checks: p95 `151.0 ms`
- all responses: HTTP 200

A minimal Python control using only the built-in SDK was also deployed while testing, but its first deployment used an incompatible external SDK setup and was not retained as a final artifact. It demonstrated that a minimal Python Worker can be packaged without the application dependency tree. The temporary controls were deleted from the checkout after the experiment and their remote Workers were deleted with Wrangler.

A thin serving Python control that reused `serving_v2.py` without FastAPI measured approximately 4 MiB uncompressed and 711 ms reported startup, versus approximately 19 MiB and 2.6 s for the full FastAPI Worker. It returned the same release `99d7088a2f0985e663a516a90c290f15`, with the same R2/hash/PIT path. This is a strong packaging/runtime hypothesis, but it was an experiment and was not promoted over the existing Worker in this milestone.

## 1105 / 503 classification

No 1105 was reproduced during the final local 100-request control runs; all full V2 persistent requests were HTTP 200 and one fresh-connection sample experienced a client connection reset. Earlier independent GitHub-hosted runs did observe Cloudflare 1105/503 during the pointer-cache experiment, including health checks. Because the minimal JavaScript control was healthy and the error was intermittent, the evidence is insufficient to classify 1105 as a deterministic application bug or a universal Cloudflare platform failure. The correct disposition remains an external/runtime availability symptom requiring a fresh multi-run control benchmark, not suppression or reinterpretation of failures.

## Smallest measured fix

The only proposed code change in this investigation is diagnostic-only isolate telemetry plus a reusable GitHub Actions benchmark workflow. No financial logic, cache policy, CURRENT semantics, release, production route, or serving data was changed.

A thin Python entrypoint is the smallest promising performance candidate because it reduced the package from approximately 19 MiB to approximately 4 MiB while reusing the existing deterministic serving module. It was not merged or made live on the custom domain because its full three-run, 100-request correctness/SLA proof has not been completed.

## Correctness gates

- Focused serving unit tests: `15 passed`
- Python compilation: passed
- `git diff --check`: passed
- live workers.dev release identity: `99d7088a2f0985e663a516a90c290f15`
- diagnostic requests continued to report `r2_gets`, cache hits/misses, and release lineage
- CURRENT changed: `NO`
- production semantics changed: `NO`
- no credentials or tokens were added

## Temporary infrastructure

Temporary control files and Workers were removed after measurement. No control Worker remains deployed. The reusable GitHub workflow remains because it provides an ongoing diagnostic mechanism without secrets.

## Remaining limitation

The primary required gate is three independent runs of 100 successful Q1 requests on the custom domain using a persistent client, plus health reliability and control comparison. The local controlled run is not a substitute for that independent external evidence. The thin entrypoint has not been proven semantically and operationally equivalent through the required Q1/Q2–Q5, PIT, corruption, shadow, and custom-domain gates.

## Exact next bounded experiment

Run the new GitHub-hosted matrix against the current full Worker, then deploy the thin entrypoint to a separate staging Worker and repeat the same matrix. Promote the thin candidate only if all three all-200 runs meet the p95 target and the full financial regression/shadow suite remains exact. Do not increase CURRENT TTL or change the canonical release while doing so.
