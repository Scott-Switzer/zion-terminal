# Zero-subscription integration: current-source audit

Date: October 14, 2026. Scope: interface reconnaissance and bounded read-only verification. This is not completion of phases A–F or financial certification of the estate.

## Repository baseline

Fetched GitHub main without checking out or modifying existing work:

| Repository | Main inspected | Local condition |
| --- | --- | --- |
| Project-Portfolio-Engine | b961925eaf28ba54076808d575e546e13e29fa88 | Secret/project-portfolio-engine-workspace has unrelated dirty files; audit reads fetched main with git show |
| zion-terminal / zion-valuation-service | f449bd848e80e80b7dbeb3eb8dd4126d587ef477 | Documents/zion-valuation-service is a worktree of zion-terminal, not a fourth independent repository; unrelated uv.lock preserved |
| DCF-stock-valuation-Engine | 4b98db82c3db0a2c093bea6afbe9d8f7ab4ab7b7 | Actual repository root is Documents/Codex/2026-10-06; find is an output directory inside it; unrelated dirty files preserved |

The local financial-assistant-gateway was inspected only for existing routing interfaces; its remote main and deployed version were not verified.

## Compatibility and ownership matrix

| Function | Owner/interface | Contract/authentication | Existing evidence and integration gap |
| --- | --- | --- | --- |
| Stored SEC history | Zion deploy/cloudflare-query/src/sec_engine_api.py; GET /private/v1/financial-history | sec-financial-engine-v2; private bearer | Fresh Apple request succeeded; source releases must be pinned across pages and consumers |
| Research identity, overview, prices, metrics, peers, screen, certification | Zion unified_research_api.py; seven /private/v1/research-* GET routes | unified-research-v1; private bearer; independent SEC/research/market identities | Already implemented; valuation route calculates ratios/FCF, not the existing DCF application's complete forecast model |
| Historical prices | Zion approved owner-only Alpaca projection; PPE acquisition/publication | Exact decimal values and source IDs; availability may be null | Existing point joins are retrospective; no continuous historical identity or corporate-action certification |
| DCF/DDM/relative calculations and saved research | DCF application and dcf_loader.py | dcf-financials-v1; numeric absolute units | Existing zion_document expects a different company packet and /v1/company/{ticker}; new history consumer must be explicit |
| Historical strategy simulation | PPE research/backtesting, portfolio_lab, empirical_strategy_campaign, engine | Multiple caller/accounting contracts | Confirmed additive-return, turnover-cost and overlapping-book defects remain; some engine paths already compound correctly |
| Raw acquisition and immutable publication | Existing PPE data plane / Zion release publishers | R2/D1 and pinned manifests | Reuse existing resources; do not create replacement stores or modify existing immutable payloads |
| Assumption benchmarks | Proposed single Damodaran provider in existing ingestion system | Separate versioned assumption registry | No verified importer/API found in the inspected core consumer files; whole-repository absence was not established |
| Query planning and explanation | Existing financial-assistant-gateway model.py and planner.py | Provider protocol, heuristic planner, cascade planner | Reuse existing router; zero-cost enforcement and new research-tool compatibility still require verification |

## Fresh bounded live evidence

Used the existing operator credential loader without printing credentials or response records. Origin: https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev. Read-only requests; no provider requests or acquisition initiated by this audit.

| Request | HTTP | Response bytes | Elapsed | Result |
| --- | --- | --- | --- | --- |
| /private/v1/capabilities | 200 | 710 | 1298.579 ms | zion-private-capabilities-v1 |
| /private/v1/financial-history, Apple 0000320193 | 200 | 105093 | 1445.778 ms | sec-financial-engine-v2; 25 observations; zero returned coverage gaps |

Apple parameters: annual; revenue, operating_income, net_income, operating_cash_flow, capital_expenditures; date_from=2021-01-01; date_to=2026-09-24; as_of=2026-09-24T00:00:00Z; limit=100. One transport attempt per request. This is transport and bounded metric coverage evidence, not complete DCF input coverage, a freshness guarantee, financial accuracy reconciliation or a latency distribution. The aggregate probe did not capture nested release metadata, so it does not certify current deployment or release pins.

Existing main's docs/unified-research-sprint/acceptance-gates.json records a separate live acceptance: 249 calculations over 20 companies, 498 independently bound inputs, 31 certification companions, 2,388 observations and 1,535 gap records. Those checks were not rerun here. Corporate actions remain failed in that report. Do not present those recorded counts as newly produced results.

## Portfolio correctness: current main versus September audit

Executed the fetched main module bodies using existing local schema dependencies, with synthetic inputs and no data access:

| Finding | Current result | Disposition |
| --- | --- | --- |
| Backtest cumulative simple returns | compute_period_returns([+0.10,-0.10], costs=0) returns cumulative gross 0.0; independent 1.1*0.9-1 = -0.01 | Reproduced defect; research/backtesting/returns.py |
| Portfolio Lab cumulative returns | Source still uses cum_gross += gross_ret and cum_net += net_ret | Confirmed by source; separate execution not performed |
| Zero-turnover transaction costs | apply_transaction_costs([0], turnover=0) returns -0.0041 with 41 bps | Reproduced defect; empirical_strategy_campaign/transaction_costs.py |
| Overlapping books | Four signals [1,2,3,4]: longs [3,2,1], shorts [2,1,0], overlap [1,2] | Reproduced defect; empirical_strategy_campaign/portfolio_formation.py |
| Unconditional PIT success | Now returns four passed=False / NOT_EVALUATED checks when evidence is absent | Prior false-success defect repaired; real cross-domain eligibility remains unimplemented in this function |
| Security identity | generate_security_id still includes ticker; merge groups by ticker and takes min(start), max(end) | Current source confirms unresolved temporal-identity risk; do not globally re-key without migration |
| Release/manifest serving | Prior audit and existing market gate inspected | Production pointer contents not freshly checked; no claim that old NaN manifest remains current |

No strategy performance may be certified from the affected paths. Geometric compounding present elsewhere does not repair these callers. Fixtures must also reject mismatched return lengths and missing observations instead of padding zeros.

## DCF input blocker

The DCF contract requires three consecutive annual periods with EBIT, D&A, operating NWC, book equity and effective taxes; separate bridge claims; dated market price and diluted-share basis. Zion's inspected history contract does not supply all these semantics. Total debt cannot populate both short- and long-term debt, and weighted diluted shares cannot establish actual outstanding shares for EV/market-cap calculations. A successful history request alone cannot produce an honest complete DCF.

An initial adapter must return a partial document with exact missing-field reasons. Multiple complete company cases require separately verified supplemental facts or explicitly attributed analyst assumptions, and suitability review. No fabricated zero claims, automatic fallback or latest-data substitution is allowed.

## Damodaran source check

Opened the official current data, archive and spreadsheet indexes. Current index lists January 9, 2026 as the broad update, but also links July 1 and April 1, 2026 country-risk updates. A single global January vintage would therefore be incorrect. The page describes absolute amounts as millions of US dollars; dataset-specific headers and methods still govern normalization.

Sources: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html ; https://pages.stern.nyu.edu/~adamodar/New_Home_Page/dataarchived.html ; https://pages.stern.nyu.edu/~adamodar/New_Home_Page/spreadsh.htm . Spreadsheet bytes, usage terms, workbook cells and archived release dates have not yet been validated.

## Changes and limits

This audit fetched remote refs, executed three synthetic reproductions and made two authenticated serving reads. Only audit/design documentation is added. No product code, source dataset, pointer, credential, deployment, schedule, paid inference, subscription or merge changed. Existing provider entitlement and cloud billing were not independently checked. Next action is review of the accompanying integration design.
