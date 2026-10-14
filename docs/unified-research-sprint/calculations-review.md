# Independent calculations review

Independent synthetic cases exercised exact arithmetic and economic-definition failures without provider-price values or external requests. Initial run: **5 passed, 9 failed** across 14 cases. The nine failures expose six distinct guard defects; production modules were not edited by this reviewer. Regression tests are in `tests/unit/test_valuation_engine_independent.py`. The owners subsequently repaired all six defects; the latest independent rerun of that file plus `test_valuation_engine.py` passes **23 tests**. Initial RED results below are retained as defect evidence, not the current status.

## Initial findings, subsequently repaired

| Severity | Concrete trigger | Observed result | Required guard |
| --- | --- | --- | --- |
| High | Derived 2024 FCF references only 2023 source leaves, while component labels are changed to 2024 | Certifier returns DERIVATION_VERIFIED | Bind each component's periods, value and source terms recursively to actual leaf IDs; metadata labels alone cannot change source periods |
| High | Complete EV components with industry absent, UNKNOWN or general_us_gaap | DERIVATION_VERIFIED | Require positive source-backed nonfinancial eligibility; absence of a financial exclusion is not eligibility |
| High | P/S consumes a negative chained market_cap | DERIVATION_VERIFIED negative multiple | Require positive market equity inputs in dependent formulas, not only during market-cap construction |
| Medium | Leverage uses quarterly EBITDA | DERIVATION_VERIFIED | Apply annual/TTM flow checks independently of a market-price role |
| Medium | FCF margin/yield receives FCF with UNKNOWN definition | DERIVATION_VERIFIED | Require verified CFO minus positive cash-PPE outflow definition and preserved component provenance |
| Medium | One-day revenue is labeled annual and used in P/S | DERIVATION_VERIFIED | Validate annual/TTM duration and period structure, rather than trusting the frequency string |

The period-transplant case demonstrates a certifier guard bypass, not evidence that the pinned native release contains transplanted observations. The source leaves in the test have genuine-looking same-issuer/hash/accession metadata and correct arithmetic, but the derived reporting period is wrong. This distinguishes arithmetic agreement from certified financial meaning.

## Guards independently confirmed

Manual Fraction/Decimal arithmetic agrees for negative operating margin, nonterminating ratios, cash-PPE free cash flow, complete EV and positive same-class market capitalization. Missing EV components produce unavailable results rather than inferred zeros. FCF mismatched periods and negative outflows are rejected. Market cap rejects weighted-average shares, another class, a different share-count date and zero shares. Financial statements published after the valuation price remain unavailable even when the query cutoff is later.

Input units and currencies are checked. Full engine/source certification still depends on an integration boundary that constructs inputs from verified server-side evidence: arbitrary `source_ids` strings and caller-supplied quality labels are not independently authenticated by the arithmetic function. On this review's initial inspection no API module imported `valuation_engine`; strict API integration was pending and is a separate deployment verification requirement.

Outstanding-share anchors preserve their original class dimensions and context date. None of the historical filing anchors establishes an interval, split factor, ADR ratio or same-day common-equity capitalization automatically. Unknown market availability remains unknown; retrospective price observation must not become a point-in-time investment input. Adjusted returns and split-adjusted share comparisons remain unavailable without verified source-backed corporate actions.

## Matched performance benchmark design

Do not compare an old financial-history request with a new overview or valuation formula request as an optimization claim: those return different work and definitions. Use the same unchanged `/private/v1/financial-history` route before and after deployment, the same fixed 20 CIKs, metrics `revenue,net_income,operating_cash_flow`, annual frequency, date_from `2015-01-01`, identical date_to/as_of/release and limit. The normalized query parameters and ordered cohort must be hashed into both receipts.

Run a first observed pass, followed by three repeated passes at concurrency three. Record monotonic request durations, HTTP status, response bytes, cache state, source_payload_bytes, archive_bytes_read_interactively, source/release/registry hashes and exact metric/period/source/value projection parity. Do not call the first pass cold unless every relevant cache reports MISS. Report cache HIT and MISS latency distributions separately, and observed-first separately from repeated warm passes. Use the same percentile method on both runs. Both BEFORE and AFTER benchmarks below have executed with the identical fixed query/cohort shape.

The unchanged native financial-history response metadata exposes source-payload size and archive interactive bytes; it does **not** expose an actual R2 GET/read count. The separate unified combined-query route now exposes measured source objects and bytes. Keep read counts null/uninstrumented unless the parent adds measured telemetry. Do not infer read counts from payload sizes or cache labels: the existing API rehashes source payloads before cached result use. A release mismatch, changed cohort/query, pagination truncation or projection mismatch invalidates the matched comparison.

## Resolved guard verification

The owner added explicit `nonfinancial_verified` eligibility for EV/leverage, annual/TTM duration checks (350–378 days), verified FCF chained definitions, positive chained market capitalization, and raw/unadjusted price/share-basis checks. The certifier owner bound derived FCF components to actual source terms and periods, closing the reproduced period transplantation. Independent positive market-cap fixtures explicitly identify RAW/NONE basis. Latest verification: 23 calculation/certifier regression tests passed, including all initially failing cases. Benchmark helper tests separately passed (two cases); Ruff checks passed.

## Executed matched BEFORE baseline

Protected receipt: `/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-research-sprint/matched-benchmark/before/receipt.json`. Script: `scripts/benchmark_unified_matched.py`. Source release is pinned to `b39a670bad0670449256e59d38bf6b9600503449baded29c25e41d5fb368c6bb`; deployment label supplied by parent is `d1-B39`. The script enforces source release but does not independently prove a Worker bundle hash.

Actual request shape: 20 fixed companion CIKs; annual `revenue,net_income,operating_cash_flow`; 2015-01-01 through 2026-09-24; as_of 2026-09-24T00:00:00Z; limit 1000; concurrency three; two passes. All 40 responses were HTTP200, untruncated, and matched expected source release. Exact duplicate-preserving financial observation/gap/source-record projection hashes matched between passes. Original public SEC responses are retained locally with protected permissions; no financial values are printed into benchmark logs.

| Pass | Cache observations | p50 ms | p95 ms | Response bytes |
| --- | --- | --- | --- | --- |
| First observed | 20 MISS | 1873.052 | 4568.614 | 3,282,421 |
| Repeat | 20 HIT | 1057.753 | 4221.019 | 3,282,401 |

Percentiles use nearest rank. These are observed API query-cache misses/hits; they do not prove absence of upstream infrastructure caches. There were no provider or SQL requests, and only the existing API query-cache writes occurred. Actual R2 read counts remain null because the API does not expose them. The post-deployment run reused the exact shape/cohort digest and passed baseline projection parity.

## Preserved intermediate AFTER comparison

The parent confirmed normal activation of Worker candidate `257e840e-6ca6-4232-8f45-0401e9caa36e`, built from commit `cef6928`, before the AFTER run. Protected evidence is `matched-benchmark/after/receipt.json` and `matched-benchmark/comparison.json` under the same artifact directory. The script enforced the same B39 financial release, fixed original 20 CIKs and query parameters; research demo expansion did not change this cohort.

All 40 AFTER requests were HTTP200 and complete. All 40 duplicate-preserving exact financial projections matched the corresponding BEFORE responses. Repeated response sizes were identical at 3,282,401 bytes. No provider or SQL requests occurred. R2 GET counts remain uninstrumented/null.

| Deployment/pass | Cache observations | p50 ms | p95 ms | Response bytes |
| --- | --- | --- | --- | --- |
| Before, first observed | 20 MISS | 1873.052 | 4568.614 | 3,282,421 |
| Before, repeat | 20 HIT | 1057.753 | 4221.019 | 3,282,401 |
| After, first observed | 20 HIT | 2488.481 | 5662.922 | 3,282,401 |
| After, repeat | 20 HIT | 2123.598 | 3693.724 | 3,282,401 |

**Observed performance is mixed.** Comparing repeat HIT passes, p50 increased 100.77% and p95 decreased 12.49%; there is no supported overall speedup claim. First-observed MISS-before versus HIT-after is not a matched cache-state comparison. This is one 20-company sample with two passes, without controlled network/Worker load; the parent was also running live demo acceptance during the AFTER measurement. The results establish preserved financial behavior and measured latency under those conditions, not a causal optimization benefit. No additional benchmark reruns were made to select a more favorable sample.

## Final stable deployment matched comparison

After the final 20-company gate passed, the parent held other agents' API activity and requested a new run against the stable candidate `be4243ee-f3d5-4920-89bd-ad2e06f16ae1`, Worker source `1af8cd0`. Evidence is protected under `matched-benchmark-final/`: a byte-identical copy of the original BEFORE receipt, the unchanged fixed 20-company cohort, `after/receipt.json`, and `comparison.json`. The intermediate mixed result remains preserved above and in its original evidence directory; it was not overwritten.

All 40 final requests were HTTP200 and complete, retained B39 source release, and matched all 40 original exact financial projection hashes. Both final passes observed 20 HIT states; repeated bytes again exactly matched 3,282,401.

| Matched repeat HIT pass | p50 ms | p95 ms | Response bytes |
| --- | --- | --- | --- |
| Original BEFORE | 1057.753 | 4221.019 | 3,282,401 |
| Final stable AFTER | 679.356 | 1806.804 | 3,282,401 |

Observed matched-repeat p50 was **35.77% lower** and p95 **57.20% lower**. The final first-observed HIT pass measured p50 706.492 ms and p95 1962.391 ms. First-before MISS versus final HIT remains an unmatched cache comparison. These measurements describe this fixed 20-company workload and final run; they do not isolate a causal code effect or control background network/platform load. No provider or SQL requests occurred. Native financial-history R2 read counts remain null/uninstrumented.

Separately, a metadata-only check of the parent's completed unified combined-screen response confirmed **33,016,204 measured source bytes, 64 measured source objects and 30 request-local cache hits**, exactly matching the bounded-cohort plan. That query explicitly requested 20 issuers, returned 17 matched issuers and explicit gaps, and stayed 538,228 bytes below the 32 MiB guard. Protected `demo-cohort-bounded-observed.json` binds the plan and actual response hashes. These measured reads belong to the combined query and must not be assigned to the unchanged financial-history benchmark route.
