# Path 1 staging acceptance — October 22, 2026 (prepared, not deployed)

## Result

The fundamentals/benchmark correctness work is complete as **prepared release
candidates and read-only evidence**. Nothing was promoted: no release object was
uploaded, no pointer or configuration value was written, no dataset job ran, and
no source release was promoted. Production website authorization was not
authorization to rebuild financial data, and none was rebuilt.

| Deliverable | Status | Evidence |
| --- | --- | --- |
| WMT revenue issuer pin (both revenue registries) | complete | `tests/unit/test_revenue_coverage_diagnosis.py` |
| Affected-issuer immutable engine-release candidate | prepared, local-only | `artifacts/path1-revenue-override-candidate/` |
| Statement-family gating diagnosis (issuer x period) | complete, read-only | `artifacts/path1-statement-family-gating/` |
| Benchmark publication/vintage evidence + coverage preflight | prepared, read-only | `worktrees/path1-benchmark-vintage/docs/benchmark-publication-evidence-2026-10-22.md` |

## 1. Costed release candidate — WMT revenue pin

The pin `"0000104169": ["Revenues"]` is in both duplicated registries
(`deploy/cloudflare-query/src/sec_engine.py` and `sec_history.py`) with fixtures
proving total-revenue selection and preserved ambiguity rules. The committed
contract registry moved with the implementation hash.

- Candidate release: `202719b583bb7a1f1b9e2c365b1193c513b5203efdbd94e8c003b8c7471db148`
  (manifest sha256 `5c8ebf1af347a7add3a7919bc78d3dc02331e567a72391b69e54504d0800ff5d`, 36,250 bytes).
- Parent (deployed) release: `b39a670bad0670449256e59d38bf6b9600503449baded29c25e41d5fb368c6bb`
  (manifest sha256 `4e0d6b9eac28ea2352dd255c4f8323f1fd4d486796063921cec691942660be7b`, 34,989 bytes).
- Registry delta is implementation-only: `491124eda9a5…` -> `9ac744a335a7…`,
  implementation `ffebb6366fad…` -> `974132184b4c…`, change class
  `REGISTRY_ISSUER_REVENUE_OVERRIDE`, affected CIKs `[0000104169]`.
- Build cost: 52 objects / 94,693,158 bytes written **locally** (manifest,
  registry, `companies/0000104169.json`, `catalog/69.json`, 48 `screen/*/`
  shards). Remote access was read-only: 151 objects / 138,075,914 bytes.
  Remote writes 0, pointer writes 0, configuration writes 0.

### Exact affected-object list

`artifacts/path1-revenue-override-candidate/affected-objects.json` lists every
object; `verification/manifest-key-diffs.json` shows the manifest delta per key.
The only manifest changes are `release_id`, `registry`/`registry_sha256`,
`registry_change` (new record), `previous_releases` (parent pinned),
`source_releases` (candidate appended), `shards` (only `69`), and
`screening_metrics` (only the 48 screen refs). `companies` is unchanged.

### Unaffected-issuer comparison (every issuer, not samples)

- 20,395/20,395 unaffected company-object references byte-identical
  (`verification/unaffected-company-objects.json`; content-addressed sha256
  equality, so equal references are equal bytes).
- 20,396/20,396 catalog entries equal, 99/100 catalog shards inherited
  byte-identical, directory inherited
  (`verification/unaffected-catalog-entries.json`).
- The frozen member reproduces the deployed WMT object byte-for-byte before the
  pin is applied (`verification/reproduction.json`), so the pin is the sole delta.

### Verified WMT semantics

`verification/wmt-semantics.json` + `verification/wmt-revenue-gap-analysis.json`:

- 19 annual revenue observations, concept `us-gaap:Revenues` only; the 10-K
  "Total revenues" line matches exactly: FY2024 648,125,000,000 / FY2025
  680,985,000,000 / FY2026 713,163,000,000.
- 309 observations of `RevenueFromContractWithCustomerExcludingAssessedTax` and
  `SalesRevenueNet` removed; 0 added; every non-revenue metric reproduces the
  deployed coverage counts; rejections did not increase; rejected contexts unchanged.
- 4 annual periods stay fail-closed gaps (2009-07-31, 2012-01-31, 2012-07-31,
  2012-10-31): WMT refiled unequal values of the same concept (or derivations
  over restated periods), and the pin selects a concept — it never chooses
  between unequal restatements. Each gap lists its conflicting raw rows.

### Rollback receipt

`artifacts/path1-revenue-override-candidate/rollback-receipt.json`
(status `PREPARED_NOT_APPLIED`): nothing was promoted, so nothing needs rolling
back today. On an approved rollout, rollback keeps
`SEC_ENGINE_MANIFEST_KEY`/`SEC_ENGINE_MANIFEST_SHA256` pinned at the parent
manifest (`…/b39a670b…/manifest.json`, `4e0d6b9eac28…`) and redeploys the prior
worker version; the parent release and all inherited objects stay immutable and
retained. Rollback stamp-enforcing consumers before unstamped upstreams.

## 2. Statement-family gating diagnosis (issuer x observation period)

`artifacts/path1-statement-family-gating/` (produced by
`scripts/diagnose_statement_family_gating.py`, read-only against the frozen
archive; policy/quarantine writes 0). Extended to the **full 20,396-issuer
universe** (`--universe`; `universe-gating.json`,
`universe-period-gating.jsonl`, `universe-quarantine-preservation.json`,
`universe-summary.md`):

- Universe classification: 15,136 materialized issuers (1,738
  `financial_statement_evidence`, 13,398 `general_us_gaap_unclassified`) and
  5,260 whole-entry quarantines (5,190 `NO_VALID_MAPPED_FINANCIAL_HISTORY`,
  70 `SOURCE_IDENTITY_MISMATCH`) — every one of the 20,396 catalog entries is
  accounted for. Evidence concept spread: `InterestAndDividendIncomeOperating`
  1,280, `LoansAndLeasesReceivableNetReportedAmount` 1,120,
  `PremiumsEarnedNet` 260, `PolicyholderBenefitsAndClaimsIncurredNet` 230.
- Per-period depth for **all 1,738 statement-family issuers** (1-80 periods
  each, median 41), from 267 MB of hash-verified frozen members: **every period
  is fail-closed** for the statement-family metrics in every issuer
  (`universe-period-gating.jsonl`), including periods with zero evidence rows
  (GE 65 of 74 periods, JPM 40 of 74).
- Universe preservation: all 15,136 policy maps match the deployed
  `statement_policy` rule exactly, zero served-coverage leaks for quarantined
  metrics, and no capex/FCF/debt restriction released anywhere
  (`universe-quarantine-preservation.json`).
- Six diagnosed issuers (deep-dive fixtures), 71-78 observation periods each;
  **every period is fail-closed** for the statement-family metrics
  (`fail_closed_every_period` true everywhere), including periods with zero
  evidence rows (GE 65 of 74 periods, JPM 40 of 74).
- GE portfolio timeline (`ge-portfolio-timeline.json`): lending evidence rows
  exist only for fiscal 2008-2011 and stop afterwards — yet gating stays in
  effect for every later period. Evidence stopping does not release any period;
  lifting the quarantine requires audited statements and reviewed accounting
  policy. Adding Goldman's concept alone is not a repair
  (`tests/unit/test_revenue_coverage_diagnosis.py`).
- Quarantine preservation (`quarantine-preservation.json`): revenue,
  operating_income, capital_expenditures, free_cash_flow and total_debt remain
  quarantined with `FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED`;
  capex/FCF/debt restrictions are not implicitly released anywhere; WMT keeps
  `total_debt: COMPONENT_NONOVERLAP_NOT_CONFIRMED`.

## 3. Benchmark publication/vintage evidence

See `worktrees/path1-benchmark-vintage/docs/benchmark-publication-evidence-2026-10-22.md`.
In short: 5 of 7 local releases now carry source-backed `published_at` (the
provider HTTP Last-Modified of the exact bytes, bound to `evidence_sha256`);
`margins` and `country_risk` have no preserved evidence for their exact bytes
and stay fail-closed at every historical cutoff. Download time and stated
vintage are disclosed but never used for availability. The read-only coverage
preflight passes 29 checks.

## 4. Staging acceptance plan (prepared; execution not authorized)

An approved rollout would, in order:

1. Upload the 52 candidate objects under
   `gold/sec-engine/releases/202719b5…/` (immutable; parent untouched).
2. Repoint both thin configs' `SEC_ENGINE_MANIFEST_KEY`/`SEC_ENGINE_MANIFEST_SHA256`
   to the candidate manifest (`…202719b5…/manifest.json`, `5c8ebf1af347…`) and
   rebuild the bundle id; deploy to staging first.
3. Staging acceptance checks: WMT revenue query returns the 10-K line values
   with `us-gaap:Revenues`; live HTTP 409 `RELEASE_MISMATCH` on the old pin;
   AAPL/PEP/JPM packet hashes unchanged; no new gap reason for other issuers;
   gateway Worker/image SHA agreement unchanged.
4. Only after staging acceptance: production rollout with the same pins.

New dataset jobs and source promotion remain a distinct, explicitly scoped
action and are **not** part of this candidate. The native limiter remains
approximate, location-local abuse protection.

## Verification gates run (this worktree)

- `tests/unit/test_revenue_coverage_diagnosis.py` + `test_sec_history.py` +
  `test_statement_family_gating_diagnosis.py`: 33 passed (incl. universe-scan
  fixtures for entry quarantines, leak detection and policy-rule matching).
- SEC/finance unit set (`-m "not integration"`): **463 passed, 1 skipped,
  10 integration deselected**. The 8 collection errors in unrelated
  retrieval-assistant modules are a pre-existing missing `diskcache` module;
  `tests/unit/test_sec_universe_readiness.py`'s integration test fails
  identically on the untouched base worktree (fixture unconfigured).

## Acceptance mapping

| Path 1 acceptance criterion | Status |
| --- | --- |
| Verified WMT semantics | 10-K line exact, `us-gaap:Revenues` only, restatement gaps documented |
| All unaffected hashes unchanged | 20,395 objects + 20,396 catalog entries equal |
| No uncertified financial-family values | statement-family quarantine fail-closed in every period |
| Honest historical availability | `published_at` from source evidence only; unevidenced vintages refuse historical cutoffs |
| New bundle only when an approved source release is actually promoted | no bundle change, no promotion, candidate remains local |
