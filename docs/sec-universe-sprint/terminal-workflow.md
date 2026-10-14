# SEC terminal research workflow

Use the existing `zion research` command with the explicit serving origin:
`https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev`.
Credentials remain in `~/.config/zion/private-api.env`; a stale configured base URL
can cause a transport error, so pass `--base-url` explicitly.

1. Search `companies --search NAME --limit 20` and select CIKs yourself.
2. Inspect `metrics --cik CIK` and `coverage --cik CIK` for supported series and gaps.
3. Fetch annual and quarterly histories, specifying `--as-of` and date bounds.
4. Run `compare --ciks CIK1,CIK2 --frequency annual --metrics revenue,revenue_growth,operating_margin,operating_cash_flow,free_cash_flow --as-of YYYY-MM-DDTHH:MM:SSZ`.
5. Screen `screen --metrics revenue_growth --minimum 0.1 --as-of YYYY-MM-DDTHH:MM:SSZ --all-pages`.
6. Inspect `provenance --cik CIK --frequency annual --as-of YYYY-MM-DDTHH:MM:SSZ`.
7. Add `--output NEW_FILE.json` to export exact JSON with SEC source records.

Comparison aligns metric, frequency, period start/end, and units exactly. Different
fiscal calendars remain separate rows. Values and differences are rational
numerator/denominator strings, preserving integers above the float-safe range.
Absent values are null, never zero; duplicate observations for the same key fail
instead of silently selecting a vintage. Cash flow support and accounting quality
remain qualified by the original response's coverage gaps, checks, and provenance.
The as-of filter is the server's filing-availability policy, not an independent
claim of point-in-time vendor completeness. No prices or peer groups are inferred.

Each comparison collects bounded histories and pins all companies/pages to one
release. Raw history pages retain source records, SEC accessions, quality fields,
and gap reasons. Exports refuse existing destinations. `provenance` is a complete
history export, so it retains numerical observations alongside their lineage.

For a reproducible end-to-end run, use `scripts/sec_research_workflow.py` with
`--ciks`, `--search`, `--as-of`, `--base-url`, and a new `--output-dir`. Its manifest
records executable commands and release ID. The screen covers the served universe;
it does not claim unmaterialized or unsupported companies meet the filter.

The history API serves base statement metrics. `compare` expands requested
`revenue_growth` and `operating_margin` into revenue/operating-income fetches
and derives exact ratios locally. Growth requires identical calendar dates one
year earlier and the same reported concept; fiscal 52/53-week shifts remain
explicit gaps. The first requested year may lack its prior year unless the fetch
window includes it. Margin requires identical periods and currency, with a nonzero
revenue denominator. Derived rows carry source IDs; raw source records stay in
`histories`. `derived_gaps` explains unavailable local calculations.

Use an ISO UTC availability timestamp at or before the release maximum. The
current screen index only supports its exact release maximum timestamp. Inspect
release metadata before choosing the workflow's `--as-of`; a later date fails
rather than silently substituting a cutoff.

## Readable terminal output

Add `--format table` to discovery, history, batch, compare, screen, coverage, or
provenance commands. JSON remains the default. `--output NEW_FILE.json` always
exports the complete returned JSON, independently of the display format. Table
values preserve exact decimals or numerator/denominator ratios; missing values,
coverage/derived gaps, release, cutoff, period, unit, and pagination remain visible.
An endpoint that does not return a cutoff or report period displays `NOT_RETURNED`.
Coverage tables show observation and gap counts rather than inferred dates.

For example, `zion research compare --ciks 0000047111,0000063276 --frequency annual
--metrics revenue,operating_margin,operating_cash_flow,free_cash_flow
--as-of 2026-09-24T00:00:00Z --date-from 2020-01-01 --format table
--output new-comparison.json` presents exact values and differences while retaining
all source records in the export.

Provenance tables construct a filing-index link only when the CIK is 1–10 ASCII
digits, positive, and the accession is the canonical `##########-##-######` form.
They use the SEC's documented symbolic path
`https://www.sec.gov/Archives/edgar/data/{integer_CIK}/{accession}-index.html`.
The accession prefix can identify a third-party filer, so it is not required to
match the issuer CIK. Invalid identifiers and absent source records display
explicit missing-link reasons. Links are format-validated; the CLI does not fetch
or launch the destination. See the SEC's official
[Accessing EDGAR Data: Paths and directory structure](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

## Final live verification

On release `b39a670bad0670449256e59d38bf6b9600503449baded29c25e41d5fb368c6bb`,
the native `zion research` entry point worked with the configured origin and no
`--base-url` override. A bounded screen scanned all 15,136 served companies:
2,986 matched annual revenue growth of at least `0.1`; 7,411 had explicit missing
revenue-growth inputs. The screen retained its `2026-09-24T00:00:00Z` cutoff and
read 21,792,748 metric-index bytes, below its 32 MiB cap.

Hershey/Mattel provided four exactly aligned annual and six quarterly rows in
2024; 43 issuer values/differences were checked with exact rational arithmetic.
Updated NIKE and Costco histories defaulted to their individual October 14
retrieval cutoffs. NIKE's quarterly export contained two newly available
post-archive observations, and Costco's annual export contained six. A comparison
with different default cutoffs failed before export; an explicit shared cutoff
succeeded. NIKE/Costco fiscal periods differ, so those rows remain separate.

Costco's 2026 annual revenue remained explicitly unavailable under the current
native mapping; the fresh cutoff does not imply every metric is supported.
Provenance leaf IDs, the versioned accounting policy, SEC link format, and JSON
exports were verified. Protected evidence and exact commands are stored at
`/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-universe-sprint/terminal/final/verification.json`.
