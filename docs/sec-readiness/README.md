# Issuer-universe valuation readiness

All 20,396 archived issuers were classified from the pinned SEC archive plus the recorded engine
checkpoints. The question this answers is not whether filings exist, but which issuers can
actually be valued today with an implemented method, and exactly what is missing for the rest.

**Release `fc3233bd6673f1cbe9502216fd74e3351ef1fd2bfc8de596e6c98ecb388dc661`** -- 21 gzipped
JSONL parts, 20,396 issuer records, 7.29 MB, published under `gold/sec-readiness/releases/` in
`financial-system-datasets` with every object verified by HEAD ETag/MD5 readback. No CURRENT
pointer was written. (An earlier release `ae8c01f6...` had the same eligibility decisions but
attributed the industrial lanes' refusals to financial filers as if they were missing concepts;
it is superseded by this release and left in place.)

```bash
python3 scripts/sec_universe_readiness.py --verify-archive       # hash the pinned archive itself
python3 scripts/sec_universe_readiness.py --total 20396          # classify, then resume freely
python3 scripts/sec_universe_readiness.py --publish              # immutable versioned release
python3 scripts/sec_readiness_index.py --publish                 # immutable screening index
python3 scripts/sec_readiness_query.py --method operating_dcf    # screen the published release
python3 -m pytest tests/unit/test_sec_universe_readiness.py -m integration
python3 -m pytest tests/unit/test_sec_readiness_published.py -m integration
```

## Where the archive loses issuers

| Stage | Issuers | Share of classified |
| --- | --- | --- |
| Archived and classified | 20,326 | 100% |
| ... with usable normalized financial history | 15,136 | 74.5% |
| ... and a complete enterprise-to-equity bridge | 15,116 | 74.4% |
| ... and a verified dated outstanding-share basis | 5,261 | 25.9% |
| ... and eligible for the implemented operating DCF | 2,765 | 13.6% |
| Eligible for *some* implemented intrinsic-value method | 5,157 | 25.4% |

70 issuers are not classified at all: their archive member's CIK does not match the index, the
same 70 `SOURCE_IDENTITY_MISMATCH` quarantines the engine recorded. The archive total of 20,396
agrees exactly with the recorded discovery count and the history total of 15,136 with the
recorded usable count -- two independent recounts of already-published numbers.

The bridge is **not** the bottleneck. It resolves for 20,305 of 20,326 classified issuers
(99.9%), including 5,189 with no normalized history at all, because the canonical claims
contract maps debt and leases from the raw filings rather than from the engine's single
`total_debt` metric, which the recorded coverage could accept for exactly one company
(`COMPONENT_NONOVERLAP_NOT_CONFIRMED`). The bottleneck is the **dated outstanding share count**:
14,229 issuers have none inside the 400-day eligibility window, and that alone stops 3,438 of
them from an operating DCF and 8,555 from comparables.

| Method (implemented today) | Eligible | Share |
| --- | --- | --- |
| comparables | 4,976 | 24.5% |
| residual income | 4,748 | 23.4% |
| DDM | 2,921 | 14.4% |
| APV | 2,769 | 13.6% |
| operating DCF (FCFF) | 2,765 | 13.6% |
| FCFE | 2,754 | 13.5% |
| bank excess return | 698 | 3.4% (of 1,738 financial filers) |

1,738 issuers are financial-statement-family filers (`financial_statement_evidence`), exactly the
recorded count, and they are routed to the specialized equity lane instead of being declared
unvaluable; 698 clear it today. For those filers the industrial lanes report a single
applicability statement, never a list of concepts the bank would not need. Only 9 issuers are
eligible for FCFE without a complete bridge -- the equity lane helps a different population than
the enterprise lanes, not a large overlapping one.

## Claim evidence across the universe

| Claim | Reported | Reviewed absent | Unavailable / conflicting |
| --- | --- | --- | --- |
| debt | 3,392 | 16,924 | 10 conflicting |
| finance leases | 1,606 | 18,720 | 0 |
| operating leases | 4,779 | 15,547 | 0 |
| cash | 5,289 | 15,037 | 0 |
| nonoperating assets | 5,389 | 14,937 | 0 |
| preferred equity | 2,190 | 18,136 | 0 |
| noncontrolling interests | 1,661 | 18,654 | 11 conflicting |
| common shares | 6,097 | -- | 14,229 unavailable |

A reviewed absence is a verified zero, not an unknown: 16,924 issuers report no borrowings at
all, and none is treated as zero debt without that review. 21 issuers have a claim refused for
valuation (`permitted_for_valuation: false` on contradictory evidence), and those refusals are
visible rather than averaged away. Of the 2,765 operating-DCF-eligible issuers, only 10 are
stopped by an unresolved bridge claim (noncontrolling interests 7, debt 3). Cover-page counts
supply 5,878 of the verified share bases and balance-sheet totals 219.

## Top missing concepts and what each would unlock

| Missing input | Issuers affected | Would unlock (as the single blocker) |
| --- | --- | --- |
| dated outstanding shares | 14,229 | 8,555 comparables, 3,440 APV, 3,438 operating DCF, 3,344 FCFE |
| dividend evidence | 14,316 | 3,176 DDM |
| book equity total | 13,944 | -- (residual income is blocked twice) |
| unlevered free-cash-flow inputs | 12,373 | 2,577 APV |
| cash flow / capex evidence | 11,343 | -- (usually paired with other gaps) |
| operating income or revenue | 10,100 | -- |
| net income | 7,109 | 661 residual income |

Ranked by issuers unlocked the priorities are unambiguous: a **dated share count** is worth more
than every other improvement combined, then **dividend evidence**, then **cash-flow and capex
mapping**. Per-method refusal breakdowns and per-issuer codes are in `coverage-summary.json`.

## Querying it

`scripts/sec_readiness_query.py` answers the screening questions straight against the published
record release, decompressing only the parts it must:

```bash
python3 scripts/sec_readiness_query.py --method operating_dcf --limit 10
python3 scripts/sec_readiness_query.py --financial            # 1,738 banks and insurers
python3 scripts/sec_readiness_query.py --missing MISSING_DATED_OUTSTANDING_SHARES
python3 scripts/sec_readiness_query.py --conflicting --any-method
python3 scripts/sec_readiness_query.py --cik 0000320193
```

That reads and filters the release, which is right for a batch question and wrong for an
interactive one: it decompresses up to 21 parts for every request. Interactive screening is
served from a derived index instead.

## The screening index the API serves

**Index `87f6bb816976417c89d6047386dc6696c18305212fb4aee4200c350f7c986d67`** -- 126 artifacts,
89,645,022 bytes, published under `gold/sec-readiness/index/` with every object create-only and
read back by HEAD ETag/MD5. The record release above is **not** modified: the index manifest names
its manifest key, digest and all 21 part digests, so a reader can verify the bytes that were
screened.

```bash
python3 scripts/sec_readiness_index.py --local      # build from the recorded release copy
python3 scripts/sec_readiness_index.py --publish    # immutable, id = digest of what it asserts
```

The index is *redundant by construction*. Every screening row is small and is published under
seven method filters, six flag filters, eleven missing-input filters, two status filters and 100
CIK remainder shards, so a question is answered by reading the pinned manifest and then exactly
one artifact:

| Artifact | Objects | Bytes | Widest object |
| --- | --- | --- | --- |
| `buckets/method-*.json` | 7 | 9,550,436 | 2,222,259 (comparables) |
| `buckets/missing-*.json` | 11 | 40,466,516 | 6,335,244 (dividend evidence) |
| `buckets/flag-*.json` | 6 | 22,059,088 | 8,745,122 (complete bridge) |
| `buckets/status-*.json` | 2 | 8,768,550 | 8,754,478 (classified) |
| `cik/*.json` (remainder shards) | 100 | 8,768,746 | 100,985 |

The manifest is 20,276 bytes and the summary 11,410. That is 4.4 KB of index per issuer, and it is the deliberate cost of never decompressing the
record release or scanning the archive to answer a screening question. The route refuses a filter
whose declared artifact exceeds 12.58 MB (12 MiB) rather than reading it blind, and the widest
artifact here is 8.75 MB, so every published filter is answerable as it stands.

### The route

`GET /private/v1/readiness` on the private thin Worker, pinned by `SEC_READINESS_INDEX_KEY` and
`SEC_READINESS_INDEX_SHA256`:

| Request | Answer | Objects read |
| --- | --- | --- |
| `?view=summary` | the counts by method, flag, missing input and status | 1 (20 KB manifest) |
| `?method=operating_dcf` | the 2,765 issuers with an implemented operating DCF, paginated | 2 |
| `?method=bank_excess_return` | the 698 financial filers that clear the specialized lane | 2 (300 KB) |
| `?missing=MISSING_DATED_OUTSTANDING_SHARES` | the 14,229 issuers with no dated share count | 2 (6.3 MiB) |
| `?cik=0000320193` | one issuer's row and the record part that holds its evidence | 2 (83 KB shard) |
| `?flag=complete_equity_bridge&method=fcfe` | the intersection, from the narrower artifact | 2 |

Filters are conjunctive and one artifact is read -- the narrowest filter of those supplied -- with
the remaining predicates applied to those rows in memory. Reading a union of two filter artifacts
would answer a different question than the one asked. Cursors are signed with the private
credential and bound to both the query and the index id, and every returned row carries the record
part it came from, so each answer connects to the existing per-company research and history
routes.

The terminal exposes it through the command it already has:

```bash
zion research readiness --view summary
zion research readiness --method operating_dcf --limit 20 --format table
zion research readiness --missing MISSING_DATED_OUTSTANDING_SHARES --limit 25
zion research readiness --cik 0000320193
zion research readiness --flag complete_equity_bridge --method fcfe --all-pages
```

### What was verified, and where it runs

The route was exercised against the real published index in two ways:

* **In the local workerd runtime** (`thin/wrangler.dev.jsonc`, seeded by
  `scripts/seed_dev_market_data.py --readiness`): `?view=summary` returned 20,396 issuers with the
  published counts reading exactly one object; `?method=bank_excess_return` returned 698 matches
  from one 300 KB artifact with a cursor and `archive_bytes_read: 0`; `?cik=0000320193` returned
  Apple from one 83 KB shard; an issuer absent from a present shard returned
  `SCREEN_CIK_NOT_MATERIALIZED` (404); a missing token returned 401; a stale `expected_index_id`
  returned 409. No errors in the Worker log.
* **In `tests/unit/test_sec_readiness_published.py -m integration`**: 9 tests read the real
  published objects through the real route and hold it to the record release -- the served method
  counts equal `method_eligibility`, the flag counts equal the release's own funnel, the 70
  `SOURCE_IDENTITY_MISMATCH` issuers carry no method verdict at all, a verified share basis and a
  missing share count are disjoint sets, and the directly fetched `summary.json` is the shape
  `?view=summary` returns.

**The route is not deployed.** It is implemented, unit-tested (10 tests in
`tests/unit/test_sec_readiness_api.py`), and verified in the local Worker; the pins are in
`thin/wrangler.jsonc` so the deployment is a single deliberate step. It is deliberately not the
same step as the pending `equity_claims` fix above: `zion-financial-serving-v2-thin-staging` is
running the previous revision and has not been moved.

## The newer SEC source release

The September 24, 2026 snapshot stays exactly as it was. The current public bulk archive was
retrieved and published beside it:

| | Pinned | New |
| --- | --- | --- |
| Snapshot | 2026-09-24 | 2026-10-15 |
| Objects | `sec/bulk/sec-nightly-20260924/` | `sec/bulk/sec-nightly-20261015/` |
| Bytes | 1,409,389,023 | 1,410,532,244 |
| SHA-256 | `e00a594c...` (declared only) | `b753b2c5f0fafb38d313f7450b35d336dfa1360cd31cf73cc432d94357d7d791` (full readback, ETag/MD5 matched) |
| Members | 20,396 | 20,461 (**+65 issuers**, 0 removed, **689 members changed**) |

Retrieval, hashes, the derived member index, the issuer difference and the publication receipts
are recorded in `sec/bulk/sec-nightly-20261015/manifest.json` (and `manifest-corrected.json`:
the first manifest compared the CIK-keyed archived index with filename-keyed members and
reported every issuer as added and removed; the corrected difference keys both by CIK and leaves
the archive hash, member index and receipts untouched). Nothing was overwritten and no serving
pointer was promoted: an un-normalized release does not become current by being stored. The
normalization and engine projection for 2026-10-15 is the next job, and until it exists a
valuation dated after September 24 cannot honestly be presented as decided on current filings.

## What is not measured

* **Sector.** The pinned source carries no usable industry code, and the engine's own policy
  records `TAXONOMY_STATEMENT_FAMILY_NOT_CERTIFIED_INDUSTRY_SIC`. The breakdown here is by
  statement family (financial vs general); a sector split needs an external identifier that is
  not in this archive.
* **Multiple share classes.** The bulk Company Facts archive contains no dimensioned facts at
  all: 0 of 20,396 issuers carry a class-scoped share fact, so per-class counts and class
  dilution stay `UNAVAILABLE`. That is a source limitation, not a finding about the issuers, and
  it needs the XBRL instance or filing cover pages.
* **Why a share count is missing for a given issuer.** The record says the claim is unavailable,
  not whether the filer never filed a count or filed one outside the recency window. The
  engine's annual weighted-average diluted-share coverage (9,298 companies) is a different
  measure and is not a substitute for a point-in-time count.
* **Assumptions.** Eligibility is a company-side input test. Every method still needs dated
  discount-rate assumptions and forecast drivers, which are assumptions, not filings.
* **The 2026-10-15 release's contents.** It is stored, hashed and diffed, but not normalized.

## Defect found by running at this scale

`equity_claims.debt_claim` refused to classify a whole class of filers: when a filer reports a
combined long-term debt total and a noncurrent portion but no current-debt total and no current
portion, the same-period comparison inspected the first key of the empty current-total mapping
and raised `IndexError`. Three verified filers never reach it. The condition is now the intended
one, the note for that case states what actually happened, and three regression tests cover the
crash, the matching case and the different-scope case (`tests/unit/test_equity_claims.py`).

This fix is **not deployed**: `zion-financial-serving-v2-thin-staging` still runs the previous
revision, so the live claims route would fail on the filers that reach that branch. Redeploying
that Worker is a separate, deliberate step because the DCF staging chain depends on it.

## Resource measurements

| Measure | Value |
| --- | --- |
| Issuers classified | 20,396 of 20,396 |
| Wall time | **99.4 seconds** (8 workers) |
| Throughput | 205 issuers/second; 708 worker-seconds |
| Decompressed source read | 19.31 GB (archive is 1.41 GB compressed, read once) |
| R2 range requests | 107 for the whole archive |
| Peak RSS | 1.18 GB (one archive handle per worker, no full copy on disk) |
| Published release size | 7.29 MB gzipped, 23 objects |
| Cloud writes | `IfNoneMatch` on every object; nothing overwritten |
| Estimated first-month cost | under $0.05 for readiness; $0.02 for the new SEC archive |

The archive is read straight from R2 over a 16 MiB block cache, and `--verify-archive`
independently recomputes the archive's declared SHA-256 in a single sequential pass, which the
recorded engine run never did. The independent check in
`tests/unit/test_sec_universe_readiness.py -m integration` re-reads 40 sampled issuers' raw
members with code written in the test and confirms the verdicts: a reviewed absence of debt is
never contradicted by an eligible borrowings fact, a verified share basis always has an eligible
share fact, and every record's member digest matches the archive bytes.
