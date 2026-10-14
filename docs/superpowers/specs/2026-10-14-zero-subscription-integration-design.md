# Zero-subscription financial-system integration design

Status: proposed for user review; implementation not started. Baseline: [current-source audit](../../zero-subscription-integration-audit-2026-10-14.md).

## Purpose and constraints

Connect the existing private terminal, stored financial-history service, DCF application and portfolio research engine. Success requires reproducible source-bound calculations and honest incomplete statuses, using existing archived/public data and free or local inference. Do not purchase subscriptions, enable paid inference, or incur unapproved infrastructure charges. Preserve unrelated local changes and immutable historical releases. No licensed records enter model prompts or new consumers without applicable permission.

## Approach selection

1. Add compatibility consumers to the existing engines (recommended). Zion owns identity, observations and evidence; DCF owns forecast valuation; PPE owns strategy/portfolio accounting. This minimizes duplicated ingestion and preserves existing clients, but requires explicit adapters for incompatible schemas.
2. Add a universal company endpoint inside Zion. Simpler for the old DCF caller, but couples company-packet composition to serving and risks implying complete financial inputs. Retain the old endpoint if needed for compatibility; do not make it the new canonical contract.
3. Merge repositories or rebuild calculations inside Zion. Highest migration cost and duplicates already working capabilities. Excluded from this design.

## First implementation slice: Zion-to-DCF and independent correctness repairs

Add an explicit native-history provider to the DCF application. Preserve the existing company-packet provider and manual/sample modes. Reuse its HTTP abstraction and validation pipeline; keep credentials server-side. Configuration selects the provider explicitly, with no silent fallback. Consume existing authenticated Zion identity/history contracts, with user-supplied CIK when dated identity cannot be resolved unambiguously.

Request exact annual metrics, cutoff and expected release; follow bounded pagination and reject release drift, loops or truncation. Freeze original decimal tokens, source IDs, registry definitions, source member/artifact hashes, accession and availability metadata in the evidence bundle. Normalize into existing dcf-financials-v1 numeric fields only at its calculation boundary, checking finite values, scale and units and retaining exact originals. Do not align fiscal periods by calendar-year labels alone.

Start with revenue, operating income, net income and nonnegative PP&E capex. Cash/debt/shares require reviewed definitions and component alignment. D&A, operating NWC, book equity, tax rates and bridge components remain null when unsupported. A total-debt amount is not an approved split. Return field-level missing reasons and distinguish sourced facts, derived facts, benchmark assumptions and analyst assumptions. Suitability is explicit; banks/insurers do not become generic FCFF candidates.

Persist the frozen financial input, assumptions, source versions, adapter version and DCF calculation version through existing research revisions/exports. Test at least three company/date cases for mapping; count a complete valuation only after all required fields pass the existing validator. If source coverage blocks completeness, continue the importer/correctness work and report that gate as failed, rather than weakening validation.

Repair PPE's reproduced arithmetic defects in isolated branches. Adopt one explicitly defined simple-period wealth recurrence for the affected callers; reject nonfinite/missing values and incompatible lengths. Define turnover units and one-/two-way cost convention before changing cost behavior. Zero turnover entails zero trading costs; fixed account fees and borrow charges must be separately named. Disallow overlapping long/short constituents and report insufficient universe explicitly. Preserve NOT_EVALUATED PIT behavior until actual evidence is supplied. Temporal identity migration is a separate versioned contract; never silently re-key existing objects or stretch point evidence into intervals.

## Damodaran ingestion and assumptions

Implement one provider in existing ingestion infrastructure after reviewing workbook usage terms. First scope: US industry beta/capital-cost/sales-to-capital tables, implied ERP history and country risk premiums; expand through explicit dataset registry entries. Preserve original workbook bytes, URL, SHA-256, retrieval time, publication evidence, effective date, region, sheet/header definition and source units. Retrieval time is not historical availability. Unknown publication dates remain unknown and ineligible for historical backtests.

Checkpoint by dataset/version/hash; atomically publish normalized immutable releases only after schema and numeric validation. A restart verifies existing bytes before reuse. Changed same-URL content creates a new version. No existing release is overwritten. Follow actual workbook hyperlinks from the indexes rather than guessing archive filenames.

Store a reviewed, versioned many-to-many issuer-industry crosswalk with effective/known-at dates, methodology, source and ambiguity status. SIC/NAICS/GICS codes do not automatically equal Damodaran labels. Ambiguous or absent mappings require explicit user selection.

Expose a separately versioned assumption API through the existing private serving boundary. Each record has dataset/release identity, region/industry, measure/unit, dates, methodology and provenance. Benchmark assumptions never enter the company-fact namespace. Consumers select source explicitly and show its valuation effect. CAPM/country exposure avoids double-counted CRP; reinvestment and terminal ROIC use unit-consistent deterministic formulas. Compare parsed cells and selected model examples against original workbooks and independent arithmetic.

## Market and portfolio integration

Reuse the existing owner-only market projection and acquisition controls. Inventory metadata before fetching new data. Any new provider operation requires known free access, remaining quota, source-specific permitted use and a bounded request plan; a configured key is not evidence of either quota or entitlement. Do not write Alpaca provider rows to the Mac under the PPE agent rules. Keep model-facing results segregated from owner market exports.

Maintain separate raw, split-adjusted and total-return fields with action IDs, formulas and adjustment policy. Unverified actions mean adjusted returns unavailable. Preserve IEX/SIP feed identity. Historical publication times, dated security identity and delistings are required before point-in-time strategy claims. Existing retrospective point joins remain labeled accordingly.

Route repaired PPE research through an immutable input bundle pinning identity, facts, prices/actions, availability, calendar, universe, cost/borrow convention and calculation versions. Reproduce one event study and one portfolio calculation independently before surfacing performance. Qlib/LEAN evaluation is deferred until an actual missing capability is identified.

## Financial AI prototype

Extend the existing financial-assistant-gateway provider protocol and heuristic planner. Deterministic routing is the default; paid/unknown-cost remote providers are disabled. Local inference is optional after installed runtime/model availability is verified. Free hosted inference requires a hard enforceable request budget and explicit fail-closed behavior at exhaustion; quota availability alone is insufficient for a zero-cost claim.

Tools perform all arithmetic and return versioned public-data evidence. A public-data evaluation corpus covers intent routing, period selection, unsupported methods, missing evidence, release drift, citations and abstention. Record latency, model calls, tokens and cost-evidence status. Do not train models or send vendor data externally in this slice. Jev remains optional until free access and terms are demonstrated.

## Verification and delivery gates

- Adapter: positive mapping plus wrong CIK, wrong unit, unavailable cutoff, missing bridge, nonfinite conversion, conflicting contexts, stale release and incomplete pagination tests. Live reads must use multiple supported companies and aggregate-only logs.
- DCF: independently reproduce forecast cash flows, WACC/terminal calculations and per-share bridge from the frozen inputs; report complete cases separately from partial mapping cases.
- PPE: +10%/-10% must yield -1%; zero-turnover fixture must charge zero trading costs; four-security books must never overlap. Add changing identities and missing observations before admitting historical performance.
- Assumptions: original-cell parity, independent normalized arithmetic, resumability, changed workbook, regional ambiguity and missing-vintage tests.
- Workflow: one authenticated client obtains identity, facts and assumptions, invokes the existing DCF and repaired portfolio engine, exports source-bound results and can replay the frozen bundle.
- AI: public corpus with per-case expected route/abstention, source-reference checks, zero paid calls and explicit cost-evidence limits.
- Deployment: record source SHA, active Worker version, exact independent release IDs and authenticated negative cases. Unit/CI success does not establish live deployment or billing. Preserve existing rollback controls and publication pointers unless a separately tested promotion is required.

Deliver focused commits, compatibility updates, reproducible commands and a phase-by-phase ledger. Work blocked on data coverage or permissions does not block independently executable phases. No merge is authorized by this design; PPE's agent guide requires explicit user merge authorization.

## Review boundary

This is architectural work. Under the loaded brainstorming skill, the next gate is user review of this written design, followed by a written implementation plan and execution-method selection. No product implementation or deployment has occurred in this turn.
