# Issuer-universe valuation readiness

All 20,396 archived issuers were classified from the pinned SEC archive plus the recorded engine
checkpoints. The question this answers is not whether filings exist, but which issuers can
actually be valued today with an implemented method, and exactly what is missing for the rest.

**Release `ae8c01f6deabf31c6db219da7ad3108fd54e55b5c1704ad9601080cd3f0e77cf`** -- 21 gzipped
JSONL parts, 20,396 issuer records, 7.29 MB, published under
`gold/sec-readiness/releases/` in `financial-system-datasets` with every object verified by
HEAD ETag/MD5 readback. No CURRENT pointer was written.

```bash
python3 scripts/sec_universe_readiness.py --verify-archive      # hash the pinned archive itself
python3 scripts/sec_universe_readiness.py --total 20396         # classify, then resume freely
python3 scripts/sec_universe_readiness.py --publish             # immutable versioned release
python3 -m pytest tests/unit/test_sec_universe_readiness.py -m integration
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
agrees exactly with the recorded discovery count, and the financial-history total of 15,136 with
the recorded usable count -- two independent recounts of already-published numbers.

The bridge is **not** the bottleneck. It resolves for 20,305 of 20,326 classified issuers
(99.9%), including 5,189 that have no normalized history at all, because the canonical claims
contract maps debt and leases from the raw filings rather than from the engine's single
`total_debt` metric, which the recorded coverage could accept for exactly one company
(`COMPONENT_NONOVERLAP_NOT_CONFIRMED`). The bottleneck is the **dated outstanding share count**:
14,229 issuers have no share count inside the 400-day eligibility window, and that alone is what
stops 3,438 of them from an operating DCF and 8,555 from comparables.

| Method (implemented today) | Eligible | Share |
| --- | --- | --- |
| comparables | 4,976 | 24.5% |
| residual income | 4,748 | 23.4% |
| DDM | 2,921 | 14.4% |
| operating DCF (FCFF) | 2,765 | 13.6% |
| APV | 2,769 | 13.6% |
| FCFE | 2,754 | 13.5% |
| bank excess return | 698 | 3.4% (of 1,738 financial filers) |

1,738 issuers are financial-statement-family filers (`financial_statement_evidence`), exactly the
recorded count, and they are routed to the specialized equity lane instead of being declared
unvaluable; 698 currently clear it. Methods that would be appropriate but are not implemented
are named in each record rather than counted.

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
all, and none of them is silently treated as zero debt without that review. 21 issuers have a
claim refused for valuation (the contract returns `permitted_for_valuation: false` on
contradictory evidence), and those refusals are visible in the record rather than averaged away.
Cover-page counts supply 5,878 of the verified share bases and balance-sheet totals 219.

## Top missing concepts and what each would unlock

| Missing input | Issuers affected | Would unlock (single blocker) |
| --- | --- | --- |
| dated outstanding shares | 14,229 | 8,555 comparables, 3,438 operating DCF, 3,344 FCFE |
| unlevered free-cash-flow inputs | 14,111 | 3,318 APV |
| cash flow / capex evidence | 13,081 | 659 operating DCF, 651 FCFE |
| book equity total | 13,944 | -- |
| dividend evidence | 14,316 | 3,176 DDM |
| operating income or revenue | 11,838 | -- |
| net income | 7,109 | 661 residual income |

Ranked by issuers unlocked, the priorities are unambiguous: a **dated share count** is worth
more than every other improvement combined, then **cash-flow and capex mapping**, then the
dividend and net-income concepts. Per-method refusal breakdowns and per-issuer missing-input
codes are in `coverage-summary.json` and in each record.

## What is not measured

* **Sector.** The pinned source carries no usable industry code, and the engine's own policy
  records `TAXONOMY_STATEMENT_FAMILY_NOT_CERTIFIED_INDUSTRY_SIC`. The breakdown here is by
  statement family (financial vs general) instead, and a sector split needs an external
  identifier that is not in this archive.
* **Multiple share classes.** The bulk Company Facts archive contains no dimensioned facts at
  all: 0 of 20,396 issuers carry a class-scoped share fact, so per-class counts and dilution by
  class stay `UNAVAILABLE`. This is a source limitation, not a finding about the issuers, and
  class detail needs the XBRL instance or the filing cover page.
* **Why shares are missing for a given issuer.** The record says the claim is unavailable, not
  whether the filer never filed a count or filed one outside the recency window. The engine's
  annual weighted-average diluted-share coverage (9,298 companies) is a different measure and
  is not a substitute for a point-in-time count.
* **Assumptions.** Eligibility is a company-side input test. Every method still needs dated
  discount-rate assumptions and forecast drivers, which are assumptions, not filings.
* **Freshness.** Everything here is the September 24, 2026 snapshot. A valuation dated after it
  cannot be presented as decided on current filings until the newer release is normalized.

## Defect found by running at this scale

`equity_claims.debt_claim` refused to classify a whole class of filers: when a filer reports a
combined long-term debt total and a noncurrent portion but no current-debt total and no current
portion, the same-period comparison inspected the first key of the empty current-total mapping
and raised `IndexError`. Three verified filers never reach it. The condition is now the intended
one, the note for that case states what actually happened, and three regression tests cover the
crash, the matching case and the different-scope case (`tests/unit/test_equity_claims.py`).

## Resource measurements

| Measure | Value |
| --- | --- |
| Issuers classified | 20,396 of 20,396 |
| Throughput | 136 issuers/second (6 workers, resumed run) |
| Peak RSS | 1.16 GB |
| R2 range requests | 12 per resume slice, one pass over each worker's contiguous archive range |
| Decompressed source read | 19.3 GB total (archive is 1.41 GB compressed, read once per range) |
| Published release size | 7.29 MB gzipped |
| Cloud writes | 23 objects, ~1 Class A unit each, `IfNoneMatch` so nothing is overwritten |
| Estimated first-month cost | under $0.05 (storage of 7 MB plus request units) |

The archive is read straight from R2 over a 16 MiB block cache with one open archive per worker;
no full archive copy is written to disk, and `--verify-archive` independently recomputes the
archive's declared SHA-256 in a single sequential pass, which the recorded engine run never did.
