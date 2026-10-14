# Incremental public SEC update candidates

`scripts/sec_incremental_update.py` stages bounded company refreshes without changing any serving pointer, Worker configuration, existing archive, or licensed dataset. Provider access is disabled unless `--fetch` is supplied. Publication requires the additional `--publish` flag and creates objects only beneath `gold/sec-engine/update-candidates/<release_id>/` in `financial-system-datasets`, with byte-for-byte readback verification.

The implementation follows the official [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [fair-access guidance](https://www.sec.gov/about/developer-resources). Companyfacts is an authoritative full company snapshot, not a list of changed rows. APIs update throughout the day, but their timestamps do not establish exact dissemination times for every financial fact. Companyfacts covers standard taxonomy, entity-wide observations; it is not comprehensive filing extraction.

## Local staging, with no provider requests

Supply a public previous engine manifest with `release_id`, `registry_sha256`, `rights: PUBLIC_SEC_ONLY`, and `licensed_sources_allowed: false`. The manifest's original bytes are hashed into the candidate. Place `<10-digit-CIK>.companyfacts.json` in the input directory, optionally accompanied by `<CIK>.previous.companyfacts.json` and `<CIK>.http.json` containing source URL, ETag, and actual retrieval time. The timestamp is evidence supplied by the fixture operator; it cannot be in the future.

```sh
python scripts/sec_incremental_update.py \
  --previous-manifest /absolute/previous/manifest.json \
  --input-dir /absolute/public-sec-fixtures \
  --output-dir /absolute/candidate-release \
  --fetched-at 2026-10-13T00:00:00Z
```

The output contains original JSON bytes under `raw/<CIK>/<body-SHA256>.json`, replacement normalized company payloads, and a candidate overlay manifest. No numeric JSON float passes through binary floating-point normalization: parsing uses Decimal and the existing exact-value SEC engine. The candidate pins the implementation hash separately from its predecessor's registry hash. Compatibility with that registry remains a required independent promotion gate.

Each changed CIK replaces its full snapshot. Observations from older snapshots are never appended to fill a missing concept, deleted context, or corrected value. Null corrections remain null; removed source IDs become explicit deletion gaps. The previous immutable release remains available to reproduce its earlier view. Within the replacement snapshot, filing revisions retain their source availability and the existing point-in-time selector. Date-only filing availability remains the conservative next-day Eastern-time policy. Observations whose availability exceeds the minimum of operator retrieval time and the individual HTTP retrieval time are excluded from candidate normalized results and counted; raw bytes remain intact. Individual company cutoffs must be honored by any future overlay reader. Unchanged companies inherit their previous snapshot and freshness, not the changed company's newer retrieval cutoff.

## Optional official SEC retrieval

```sh
python scripts/sec_incremental_update.py \
  --previous-manifest /absolute/previous/manifest.json \
  --input-dir /absolute/public-sec-cache \
  --output-dir /absolute/candidate-release \
  --fetch --cik 0000320193 \
  --user-agent 'Your Organization actual-contact@your-domain.example'
```

The fetcher permits only official `https://data.sec.gov` companyfacts and submissions resources with validated ten-digit CIKs; staging currently fetches companyfacts only. It forbids redirects, uses a descriptive contact User-Agent, and runs sequentially at no more than two requests per second. Coordinate other jobs to stay within SEC's aggregate ten-request-per-second limit across all machines. Only transient 429/5xx responses receive up to three attempts and bounded delays. A 403 stops immediately. HTTP bodies are capped at 24 MiB and calls time out after 30 seconds. At most 100 companies are accepted per command.

A local `<CIK>.http.json` ETag enables `If-None-Match`; HTTP 304 creates no changed company. Source HTTP metadata is persisted inside the normalized lineage. The command does not maintain a mutable cache cursor: an operator must seed the next input cache from the candidate's raw bytes and lineage metadata, or implement an independently reviewed scheduler. Submissions support is a retrieval primitive, not yet a discovery/scheduling pipeline, and does not imply filing completeness.

Add `--publish` to store the candidate create-only in R2. Reusing a key with different bytes fails. A repeat invocation with identical previous manifest, input bytes, metadata, and retrieval timestamp produces the same release ID. Unchanged source bytes are omitted when a previous raw snapshot is supplied.

## Promotion remains separate

There is no promotion flag. Existing serving code does not yet resolve these overlay manifests. Before enabling a candidate, an explicit approved promotion gate must validate registry/implementation compatibility, independently reconcile changed company results, honor each source cutoff and inherited freshness, verify expected-source deletions and source-lineage hashes, and generate a regular serving catalog or reviewed overlay reader. The script never automatically enables a candidate or expands licensed-data access.

Coverage limitations include SEC API processing lag, date-only filing availability, absent custom/entity-segment concepts, missing previous raw snapshots (`NO_PRIOR_RAW`), and removed observations accessible only through their original release. A newer snapshot cannot retroactively certify a fact that was not present in an older archived snapshot.
