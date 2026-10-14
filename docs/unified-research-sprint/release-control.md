# Staging release control

`controlled_research_promotion.py` activates an already uploaded immutable Worker version. It verifies the publication hash, exact SEC and research manifest pins, committed Worker sources, pinned calculation implementations, and the actual active previous version before changing traffic. Split traffic is rejected.

Candidate validation checks authenticated overview, metric certification and private historical prices, then reproduces an explicitly selected historical SEC release. Any activation or validation failure automatically restores the previous immutable Worker version and checks both its active version ID and historical observation hash. A failed restoration is reported as `ROLLBACK_UNVERIFIED`, never as success.

Run first with `--rollback-drill` to exercise a deliberately failed candidate validation and verified restoration, then run without that flag against the same uploaded candidate. Separate evidence paths retain both results. Upload with Wrangler `versions upload --keep-vars`; activation uses `versions deploy VERSION@100`. This is a staging-only operator workflow, with no recurring scheduler and no production CURRENT mutation.

SEC updates must first pass the existing immutable SEC publication checks and publish a compatible research root pinning that new SEC manifest. The controller rejects mixed roots. Original archives and releases remain available; promotion does not rewrite them.

The previously deployed Worker source was `1e7e3d2`; comparison against `cd9ba98` showed no changes under `deploy/cloudflare-query`. That newer commit added operator tooling rather than unshipped Worker source. Actual version IDs and post-promotion receipts belong in acceptance evidence.

The controller waits for the serving edge's actual Worker version, retains failed deployment-state output in protected diagnostics, and allows bounded route-propagation retries. `--research-cohort` plus a new `--research-validation-output` includes the complete twenty-company demonstration inside automatic rollback. Initial live validation exposed mixed instant/duration sorting and an uncovered debt-symbol selection defect; both received regression tests. The failed candidate was automatically restored when the full demonstration failed. These attempts remain in protected evidence rather than being replaced by the successful attempt.
