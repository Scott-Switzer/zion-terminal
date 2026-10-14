# Public SEC historical security identity

The final repair provides **21 unambiguous 2019 issuer/security points**, selected from existing provider-symbol coverage and literal official SEC filing covers. Two additional candidates, JNJ and PG, remain explicitly ambiguous because their repeated ticker observations have different class-dimension scopes. The resolver does not relax that ambiguity to meet a numerical target.

The complete public source fixture contains 36 issuers, 117 listing observations and 54 DEI outstanding-share facts across the initial and historical repair phases. Those totals include other dates and debt listings; they are not 117 equity joins. The market and certification workstreams separately validate exact stored session membership and source-bound financial histories at each selected historical point.

## Final evidence

All artifacts below are protected local files in `/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-research-sprint/`:

- `security-identity-anchors-final.json`: complete PUBLIC_SEC_ONLY source fixture; SHA-256 `151c1243fc1a40fee4e60be35796611a875ac3db53cca8fc66585bf452ff9413`.
- `historical-identity-resolver-final.json`: 21 PASS and two AMBIGUOUS_SECURITY_IDENTITY failures across 23 tested issuer/date candidates; exact symbol, CIK, date and original source hash are retained.
- `public-identity-repair*/receipt.json`: acquisition receipts and literal accession-specific official source URLs. Exact original HTML is retained as protected `.html.gz`; fixture SHA-256 values bind decompressed source bytes.
- `security-review-incident.json`: separate disclosure of incidental potentially licensed derived output from an earlier broad artifact search. Its provenance remains unconfirmed. No affected values are copied into these source fixtures or this report. Artifact text searches stopped; subsequent work used explicit metadata projections and approved public SEC bytes.

Executed acquisition totals: **88 official HTTP requests, 44 primary-document candidates, 12,888,067 compressed bytes retained**. Requests were paced below two per second, individual source responses capped at 24 MiB, and compressed local retention capped at 50 MiB. The operator's final probe guard was 90 requests; it was an internal safety bound extended after the existing-resource evaluation, not a user prohibition on further public filing research. No 403 occurred; that status would have stopped acquisition without retry. No cloud objects, CURRENT pointers, runtime credentials, SQL or provider datasets were written by this identity workstream.

## Exact historical points

| Symbol | CIK | Filing point |
| --- | --- | --- |
| ABT | 0000001800 | 2019-10-31 |
| GE | 0000040545 | 2019-10-30 |
| GS | 0000886982 | 2019-11-01 |
| HD | 0000354950 | 2019-11-26 |
| HON | 0000773840 | 2019-10-17 |
| HRL | 0000048465 | 2019-09-03 |
| HSY | 0000047111 | 2019-10-25 |
| INTC | 0000050863 | 2019-10-25 |
| KO | 0000021344 | 2019-10-24 |
| LOW | 0000060667 | 2019-12-04 |
| MA | 0001141391 | 2019-10-29 |
| MCD | 0000063908 | 2019-11-04 |
| MDT | 0001613103 | 2019-12-03 |
| MRK | 0000310158 | 2019-11-05 |
| MS | 0000895421 | 2019-11-05 |
| NKE | 0000320187 | 2019-10-04 |
| PEP | 0000077476 | 2019-10-03 |
| PFE | 0000078003 | 2019-11-07 |
| TGT | 0000027419 | 2019-11-27 |
| UPS | 0001090727 | 2019-10-29 |
| WMT | 0000104169 | 2019-12-04 |

JNJ 2019-10-28 and PG 2019-10-22 are excluded. V has no matching stored 2019 price coverage. AAPL 2019-10-31 has no matching stored session; its existing bars are 2016 canaries. These exclusions remain explicit rather than being backfilled from current tickers. The matched financial performance baseline uses its original separate 20-CIK cohort; changing this research demo cohort does not alter that benchmark.

## Validity, knowledge and security scope

`security_identity.py` checks original HTML SHA-256, the hash-verified existing SEC metadata payload, CIK, accession, official accession-specific SEC URL and supported 10-K/Q form. The supplied filing date comes from existing public source metadata. Identity is valid **only on that observed filing date**. The XBRL context's fiscal period or instant is preserved separately and never expanded into a ticker-validity interval.

Evidence includes literal symbol, exchange and context quotes. Matching requires the same issuer, period and class dimensions. An exchange context may add only the explicit listing-exchange axis; when the symbol already has an exchange axis, its member must match exactly. This preserves ABT's explicit XNYS listing without conflating a second XCHI listing. Unknown typed dimensions, another class or period, conflicting exchange names and unresolved scope ambiguity fail closed.

No timezone-supported source acceptance timestamp was available in this metadata. `known_at` stays null with `SOURCE_DATE_ONLY_NO_INTRADAY_PIT`; retrieval timestamps remain separate. Intraday `as_of` identity requests fail closed without exact source knowledge time. Financial history separately uses `DATE_ONLY_NEXT_DAY_ET_V1`: the existing SEC filed date is conservatively available at next-day midnight America/New_York. A cutoff at the filing-day UTC start excludes that same-day filing and can select older financial evidence. This is a documented date-only availability policy, not a literal acceptance timestamp. These are retrospective date joins, not evidence of investment-time provider-bar availability. Literal matching also does not establish a singleton common class, ADR ratio, split factor or corporate-action history.

DEI `EntityCommonStockSharesOutstanding` retains its own context instant, class dimensions, shares unit, exact decimal scaling, source quote and hash. Only supported dot-decimal transforms are interpreted. Malformed counts, non-shares units, fractional/nonpositive results and unknown transforms are quarantined. Weighted-average shares are never substituted, and multiple classes are not automatically summed. A filing-date price does not automatically match an earlier share-count context date.

## Verification and initial phases

Twelve focused identity tests pass, covering source proofs, distinct filing/share dates, units/scaling/classes, no weighted-average substitution, timezone rejection, exact-point boundaries, ambiguity, interval tampering and explicit exchange-axis separation. Ruff checks pass. Native actual-source parsing produced the final fixture; private provider values were not sent into the parser tests or chat.

The initial bounded phase (44 requests) found 21 issuer anchors across mixed dates but only two demonstrated price overlaps, WMT/PFE; most original anchors were in 2020 while selected stored bars were from 2019. That was an intermediate coverage finding. The authorized follow-up selected 2019 filings from existing full-year provider-symbol coverage, produced 23 candidate points and retained 21 unambiguous points after excluding JNJ/PG. Original phase receipts and fixture hashes remain preserved rather than rewritten as if the first attempt had already succeeded.

Relevant source specifications: [SEC structured data](https://www.sec.gov/structureddata), [XBRL context semantics](https://www.xbrl.org/Specification/XBRL-2.1/REC-2003-12-31/XBRL-2.1-REC-2003-12-31+corrected-errata-2013-02-20.html), and [Alpaca historical bars endpoint](https://docs.alpaca.markets/us/reference/stockbars). The separate market workstream verifies actual stored request metadata before deriving documented default USD while preserving original UNKNOWN.
