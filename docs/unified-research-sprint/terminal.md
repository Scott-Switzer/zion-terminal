# Unified research in the existing terminal

The existing authenticated `zion research` entry point now has additive
`identity`, `overview`, `prices`, `valuation`, `peers`, `market-screen`, and
`certification` actions. Existing SEC `companies`, `history`, `batch`, `compare`,
`screen`, `metrics`, `coverage`, `jobs`, and `provenance` behavior remains available.
No competing application, data acquisition, provider initialization, or LLM call
is introduced by these actions.

## API contract

All actions use authenticated GET requests under `/private/v1/`:

| CLI action | API suffix | Entity selection |
| --- | --- | --- |
| identity | research-identity | Exactly one `cik`, `symbol`, or `security_id` |
| overview | research-overview | CIK, or symbol with an exact supported `date_to` filing point |
| prices | research-prices | Exactly one `cik`, `symbol`, or `security_id` |
| valuation | research-valuation | CIK required |
| peers | research-peers | Explicit 2–20 distinct ten-digit CIKs |
| market-screen | research-screen | Explicit 1–20 CIKs, or a published approved cohort |
| certification | research-certification | CIK required |

Responses use `schema_version: unified-research-v1`, SEC `release_id`, financial
availability `as_of`, nullable `market_release_id`, `observations`, `prices`,
`coverage_gaps`, `source_records`, `quality`, `limits`, and `provenance`. Identity
is returned explicitly when supported. Financial availability, price session/
timestamp, and historical identifier validity are distinct fields.

Price rows contain `session_date`, exact `value_decimal`, `unit`, `currency`,
nullable `available_at`, `security_id`, `source_ids`, and `quality`. A null
availability timestamp explicitly prevents a historical point-in-time claim.
Valuation observations carry exact decimal or numerator/denominator strings,
formula ID/version, inputs, source IDs, unit, and quality. A null value remains
missing; the CLI does not compute a substitute EBITDA, debt definition, price,
share count, identifier mapping, or peer group.

Common options are `--as-of`, `--expected-release-id`, and, for market-bearing
actions, `--expected-market-release-id`. Prices accept `--date-from`, `--date-to`,
`--limit`, `--cursor`, and `--all-pages`. Certification and market-screen also
support bounded pagination. Pages must remain on the same SEC and market
releases; drift fails before a partial export. Existing limits remain 100 pages,
2 MiB per response, and 20 MiB total collected responses.

The combined screen uses `--fundamental-metric`, `--fundamental-minimum`,
`--fundamental-maximum`, `--market-metric`, `--market-minimum`, and
`--market-maximum`. Thresholds without their corresponding metric are rejected.
The server remains responsible for permitted sources, exact calculation rules,
and metric certification; the client never joins anonymous market rows to an
issuer by guessed ticker equivalence.

## Output and workflow

JSON remains the default. `--format table` shows exact values, formula versions,
quality, missing components, period/unit, and both source releases. `--output
NEW_FILE.json` always writes the full returned JSON, independent of display.
Existing exports refuse replacement of an existing file.

Supported retrospective workflow using two independently reconciled 2019 points
(Walmart 2019-12-04 and Pfizer 2019-11-07):

```text
zion research companies --search WALMART
zion research identity --symbol WMT --date-to 2019-12-04 --format table
zion research overview --cik 0000104169 --format table
zion research history --cik 0000104169 --frequency annual --as-of 2019-12-04T00:00:00Z
zion research prices --symbol WMT --date-from 2019-01-01 --date-to 2019-12-04 --all-pages --format chart --output new-wmt-prices.json
zion research valuation --cik 0000104169 --as-of 2019-12-04T00:00:00Z --metrics revenue_growth,operating_margin,free_cash_flow --format table
zion research peers --ciks 0000104169,0000078003 --metrics operating_margin,free_cash_flow --as-of 2019-11-07T00:00:00Z --format table
zion research provenance --cik 0000104169 --frequency annual --as-of 2019-12-04T00:00:00Z --output new-wmt-provenance.json
zion research certification --cik 0000104169 --as-of 2019-12-04T00:00:00Z --all-pages --format table
zion research market-screen --ciks 0000104169,0000078003 --fundamental-metric revenue_growth --fundamental-minimum 0.1 --market-metric close --market-minimum 0 --output new-screen.json
```

Prices and the combined screen are retrospective: omit `--as-of` for market
requests because historical price publication times are unavailable. CIK-based
prices require the exact identity point via `--date-to`. Screen market filters
support `close`; fundamental filters support `revenue_growth` and
`operating_margin`. Each match exposes its own financial cutoff, filing identity
point, and price session. A common cutoff in peers does not imply identical
fiscal periods; returned period fields remain authoritative.

Market capitalization, price/sales, and related enterprise-value calculations
remain unavailable when dated outstanding share classes, publication times,
adjustment evidence, or other required definitions are missing. These examples
request supported interfaces; live activation verification is recorded separately.
The expanded historical cohort must pass literal identity, price-session, and
exact-cutoff proof gates before publication or demonstration.

`--format chart` is opt-in for `prices` and single-metric `valuation`. Formula charts retain exact numerator/denominator labels, formula and source versions, and explicit missing-period gaps. For example:

```text
zion research valuation --cik 0000104169 --metrics operating_margin --frequency annual --date-from 2014-01-01 --date-to 2019-12-04 --as-of 2019-12-04T00:00:00Z --format chart --output new-wmt-margin-history.json
```

Mixed metric, issuer, unit, currency, or frequency scopes are rejected. Formula heights are illustrative; a nonterminating fraction remains a fraction.

For prices, The compact text plot uses
returned observations only, at most 120 visual points, with every exact original
value/date/unit printed below. It does not interpolate missing sessions. Visual
heights are illustrative, not rounded financial outputs. A single observation is
labeled `SINGLE_OBSERVATION_NO_TREND`; an unavailable history is labeled
`NO_AUTHORIZED_PRICE_OBSERVATIONS` with its gap reasons. Mixed security, currency,
unit, adjustment scopes, duplicate dates, or inexact values are rejected. Source
quality, adjustment policy, and absent price-availability timestamps stay visible.

Licensed/private rows must remain in authorized local exports and out of model
contexts. Demonstration verification should process permitted returned rows
locally and report only aggregate outcomes, identifiers, rights metadata, and
explicit coverage limitations to the agent.

Server integration now provides seven authenticated read-only research routes over a separately pinned unified manifest. The unified release identifier is returned separately from the SEC and owner market releases. Signed pagination binds all three versions; explicit historical market cutoffs fail closed because provider publication times are unestablished. Combined fundamental/close screening uses only exact filing identity points and matching historical reconciliation proofs, with the financial cutoff explicitly identified as a conservative analysis policy.

The request budget is 32 MiB cumulative source reads, a 2 MiB response, at most 20 requested peers, at most four concurrent storage reads, and a 25 second timeout. No query path writes caches, queues jobs, or acquires provider data. The staged owner market route additionally requires bearer authentication when `OWNER_ONLY_MARKET_DATA=true`; unrelated public SEC routes keep their existing access behavior.

Local verification: 57 focused API, native CLI, certification, and independent calculation tests passed. This is implementation evidence; activation and real end-to-end unified queries remain separate deployment checks.
