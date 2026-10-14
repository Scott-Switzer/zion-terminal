# Matched SEC financial-history benchmark

The final stable deployment preserved **all 40 exact financial projections** and measured lower repeated cache-hit latency than the original baseline. The unchanged endpoint, original 20-company cohort, annual revenue/net-income/operating-cash-flow metrics, dates, B39 source release, page limit and concurrency three were fixed across runs.

| Repeat pass | Cache state | p50 ms | p95 ms | Response bytes |
| --- | --- | --- | --- | --- |
| Original baseline | 20 HIT | 1057.753 | 4221.019 | 3,282,401 |
| Intermediate deployment | 20 HIT | 2123.598 | 3693.724 | 3,282,401 |
| Final stable deployment | 20 HIT | 679.356 | 1806.804 | 3,282,401 |

Final observed repeat p50 was 35.77% lower and p95 57.20% lower than baseline. Percentiles use nearest rank. The intermediate mixed result remains preserved. The parent paused other agents' API work during the final run; background network/platform load remains uncontrolled. These are descriptive measurements for this workload, not isolated causal attribution. First-observed baseline cache MISS and final HIT passes are not compared as matching cache states.

Protected receipts and exact public SEC responses are under `/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-research-sprint/matched-benchmark/` and `matched-benchmark-final/`. No provider or SQL requests occurred. Actual R2 read counts for this native history endpoint are uninstrumented/null.

A separate live combined query explicitly requested the bounded 20-company research cohort and measured **33,016,204 bytes, 64 source objects and 30 request-local cache hits**, exactly matching its plan. It returned 17 matched issuers with explicit coverage gaps and retained 538,228 bytes of headroom below 32 MiB. Its measured reads are not assigned to the history benchmark. The original research demo and the fixed benchmark cohort remain preserved alongside the separately selected bounded research cohort.
