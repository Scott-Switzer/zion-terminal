# Zero-subscription integration implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Ship corrected portfolio accounting, a native Zion financial-history consumer, and reusable public valuation benchmarks in the existing engines.

**Architecture:** Preserve repository ownership and existing routes. Native history maps into the existing DCF document with null gaps; public assumptions remain separately versioned. All computation stays deterministic.

**Tech Stack:** Python, pytest, existing HTTP clients, openpyxl/pandas workbook readers, existing Cloudflare serving.

**Spec:** ../specs/2026-10-14-zero-subscription-integration-design.md, approved October 14; user's subsequent priority ordering governs execution.

## Global Constraints

- No new subscriptions, paid inference, unapproved infrastructure charges or production pointer changes.
- Preserve dirty checkouts; use isolated feature worktrees.
- Missing inputs remain missing; historical results stay unvalidated until reproduced.
- Keep private market/vendor rows out of model contexts and logs.
- User explicitly authorizes immediate native execution after planning, with no routine approval stops.

## Review Focus

- Nonfinite returns or mismatched arrays must fail before emitting partial results.
- Zero turnover is zero traded notional; cost sensitivity reports effective cost rather than an unscaled rate.
- Small/tied universes cannot select the same security on both sides.
- Financial pages cannot switch source releases or silently lose source records.
- Workbook percent formats, duplicate labels and unknown vintage dates must not invent assumptions.

### Task 1: Correct portfolio arithmetic and accounting

**Files:** research/backtesting/{returns,metrics}.py, research/portfolio_lab/portfolio_returns.py, research/empirical_strategy_campaign/{transaction_costs,portfolio_formation}.py; new research/return_accounting.py; tests/test_return_accounting.py.
**Interfaces:** Existing public signatures remain; shared validation/wealth helpers consume finite simple-period returns; transaction costs use traded-notional/NAV per observation and report effective bps.

- [ ] Write regressions: +10%/-10% = -1%, relative drawdown 10%, zero/half/double turnover costs, small universes and missing/nonfinite returns.
- [ ] Run regressions and verify failures reproduce the audited defects.
- [ ] Implement geometric wealth and relative drawdown, validate alignment, scale trading costs by turnover, reject infeasible disjoint books.
- [ ] Run targeted suites and full prescribed Python suite; record unrelated failures explicitly.
- [ ] Commit corrected code and tests; do not certify old strategy results.

### Task 2: Native Zion history provider

**Files:** DCF zion_history_provider.py, dcf_loader.py, app.py provider discovery; tests/test_zion_history_provider.py; scripts/check_zion_history.py.
**Interfaces:** load_history(ticker, asof, http, *, cik, base, token, expected_release_id=None) returns dcf-financials-v1; mapping retains exact source envelope and per-field missing reasons. CIK is explicit until source-backed identity is available.

- [ ] Write tests for three annual periods, period/source/unit mismatch, release drift, pagination, null missing inputs and conflicting facts.
- [ ] Run red tests; implement bounded history fetching and mapping into the existing document.
- [ ] Expose provider through existing API, preserving the old company-packet provider.
- [ ] Run DCF suite; exercise authenticated public SEC reads for multiple companies; freeze protected evidence and print aggregates only.
- [ ] Attempt a complete valuation using source-verified supplement inputs; if unavailable, retain blocked completeness and continue Task 3.
- [ ] Commit working adapter, probe and tests.

### Task 3: Damodaran public benchmark ingestion

**Files:** PPE ingestion/damodaran/{__init__,__main__,registry,importer}.py and tests/test_damodaran.py; DCF valuation_benchmarks.py and tests/test_valuation_benchmarks.py.
**Interfaces:** import_workbook(dataset_id, raw_bytes, metadata, output_root) preserves raw/hash/normalized release and supports restart; load_benchmark(release, industry, measure, as_of) selects an explicit sourced assumption.

- [ ] Inspect official current/archive/model indexes, terms and exact workbook headers for ERP, beta, capital cost, margins, reinvestment and multiples.
- [ ] Write red parser/restart/units/duplicate/vintage tests and benchmark-consumer tests.
- [ ] Implement versioned registry and reusable XLS/XLSX normalization, immutable raw preservation, checkpointed CLI and explicit assumption selection in the existing DCF model.
- [ ] Retrieve a bounded public workbook cohort; reconcile selected cells and calculations independently; never promote retrieval time into historical availability.
- [ ] Run targeted and repository suites; commit importer and consumer independently.

### Task 4: Combined verification and delivery

**Files:** scripts and acceptance receipts adjacent to owning implementations; update execution ledger.
**Interfaces:** pinned public financial observations + explicit assumption selection -> existing deterministic valuation; repaired synthetic portfolio fixture -> independently reproducible result.

- [ ] Run one combined native workflow, report complete versus blocked components, preserve all release identities.
- [ ] Obtain fresh independent whole-change review, address material findings with regression tests.
- [ ] Verify diff checks, commits, clean worktrees and unchanged production pointers (no writes performed).
- [ ] Report actual implementation, test/live evidence and blockers; no merge or production promotion without required authorization.

## Execution ledger

Ruling: native Git worktree fallback is necessary because this chat's root is not a Git repository and the native creation tool has no repository-path argument. Sibling worktrees protect both dirty checkouts without editing their ignore files.

Ruling: the user explicitly requests planning followed immediately by implementation and disallows routine approvals; this supersedes the skill's routine plan-review pause.
