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
