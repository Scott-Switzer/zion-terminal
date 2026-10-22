# Zion Path A verification — 2026-10-22

## Result and scope

The configured full suite passes, including real measured benchmarks and the
independent pinned-archive audit. No source data, release ID, collector, source
pointer, credential or deployed service was changed by this repair.

Two runs distinguish the concurrent working tree from the actual committed code:

| Target | Result | Meaning |
|---|---|---|
| Existing worktree with concurrent SEC edits | 1,385 passed, 3 skipped, 1 warning | Current local combination works; not all of these edits belong to Path A |
| Clean Path A committed worktree at `7170187` | 1,364 passed, 3 skipped, 1 warning | Reproducible committed-code result, excludes 21 tests in unrelated uncommitted SEC work |
| Dedicated real benchmark run | 11 passed; 19 intentionally skipped by `--benchmark-only` | All formerly missing benchmark fixture cases actually measured |
| Independent archive audit | 1 passed; 40 issuers checked | Not skipped or inferred from fixture counts |
| Independent final code review | No critical/important findings; 34 focused tests passed | Read-only reviewer, no changes made |

The three skips remain explicit capability limits:

- `tests/unit/test_arelle_xbrl.py`: Arelle is absent; the XBRL verification profile is not satisfied.
- `tests/unit/test_sec_metric_certification_builder.py`: DuckDB is absent.
- `tests/unit/test_sec_universe_basin.py`: PyIceberg is absent.

The warning is Starlette's existing httpx TestClient deprecation. No broad dependency
upgrade or optional-profile installation was used to hide or repair unrelated work.

## Benchmark repair

Python 3.11.15 and pytest 9.1.1 were already installed. `pytest-benchmark` was declared
in the `dev` extra and the existing local lockfile, but not installed. Plugin
autoload was not disabled. The minimal environment repair installed
`pytest-benchmark==5.3.0` plus its `py-cpuinfo2==10.1.1` dependency using `uv pip`
against the existing interpreter. No new virtualenv, pip bootstrap, broad sync or
lockfile rewrite was performed.

`README.md` now uses `--benchmark-disable` to execute benchmark-backed functions
once in a fast correctness run. `-p no:benchmark` removed the fixture itself and
was the wrong instruction for those tests. Measured timings remain a separate
report; no unsupported performance pass threshold was introduced.

Local timing report: `/tmp/zion-benchmark-2026-10-22.json`.

## Pinned fixture and independent audit

The old test chose the newest local summary by mtime, then read a glob of locally
present shards. The inspected caches held summaries/manifests but none of their
21 declared shards. An empty glob therefore appeared as zero classified issuers.

The new loader requires `ZION_READINESS_RELEASE_DIR` and
`ZION_READINESS_RELEASE_ID`, verifies publication/manifest identity, safe filenames,
byte sizes, SHA-256 values, record counts, unique issuer identities and total
coverage, and rejects incomplete/corrupt fixtures before financial assertions.
The read-only materializer retrieves receipt-listed immutable objects only; it
neither lists remote prefixes nor writes source objects nor rebuilds data.

Verified fixture:

- Published release: `94635c4377880ea5654583d90828fbcd77c50a4fd66cd828db0d077acf72ce24`.
- Manifest SHA-256: `1cd9b9c2180a683ae0e388caaa344eaac8d698d5c3b0ce3d945bce4fc45379c6`.
- Engine release: `ac8f14de487eee6b78fd6c108e8611af8f326d864a162b00d5308f6c90abfd18`.
- Engine manifest SHA-256: `ba4e509def154799a6f85e1b16bfff63718e3a671e53b5f6586bff683ebed430`.
- Member index SHA-256: `05ac81aa32d18abe9c0c266e57016315ff50ea4b5dfc8cacc080636f87ff5ed9`.
- 21 verified shards, 20,396 total issuer records, 20,326 CLASSIFIED records.
- Receipt-listed materialization: 9,365,708 bytes, only existing objects.
- Independent sample: 40 issuers; debt/share assertions, CIKs, ZIP metadata and member hashes passed.
- Dedicated audit: 46 archive range requests, 47,288,799 bytes, 1 MiB blocks and four cached blocks. No full ZIP download.
- Audit source remains the pinned September 24 archive. This does not certify newer archive vintages or the entire financial universe's readiness.

The materializer also copies separately pinned engine/member-index metadata into
an audit context and verifies its pairing with the readiness manifest. Legacy
local parent directory names are not release identities; manifest/receipt fields
are authoritative.

## Reproduce

From the relevant clean committed checkout, reuse the existing Python interpreter.
The local fixture cache is ignored by Git; source records and credentials are not
part of this report or any commit.

```sh
export ZION_READINESS_RELEASE_ID=94635c4377880ea5654583d90828fbcd77c50a4fd66cd828db0d077acf72ce24
export ZION_READINESS_RELEASE_DIR=/Users/scottthomasswitzer/Documents/zion-valuation-service/.cache/readiness-audit/$ZION_READINESS_RELEASE_ID
PYTHONPATH=src /Users/scottthomasswitzer/Documents/zion-valuation-service/.venv/bin/python -m pytest -q -rs
```

The fixture can be restored on another approved host with
`scripts/materialize_readiness_test_fixture.py --help`, an explicit receipt hash,
release ID, engine manifest and member-index hash, and privately configured R2
access. Missing private access is an incomplete audit, not a substitute source or
permission to disable the financial assertions.

CI's minimal synthetic contract job now explicitly deselects integration tests.
It does not imply that the private archive audit ran. The complete local result
above includes the configured audit and real benchmark plugin.

## Bundle integration and remaining rollout gates

Path A committed the source stamp and bundle checks separately from existing dirty
SEC release/config changes. Only `BUNDLE_SHARED_SNAPSHOT=true` was added to the two
committed thin configurations; their preexisting source pins were preserved.
The shared DCF module/import/package entry is committed atomically as `616eb3f`;
Gateway/MCP is reconciled onto canonical main as `1e7c2ce`. Dedicated integration
workspaces and exact handoff details are recorded in the financial-system-core
Path A handoff.

The source branch builds on existing local serving checkpoint `935e4d8`, which
already includes historical source/serving work not yet present on remote main.
Do not blindly merge that legacy history or deploy an older main over the active
service. Compare the reviewed checkpoint/configuration with the active deployment
before the coordinated rollout. No deployment is claimed by these tests/dry runs.
