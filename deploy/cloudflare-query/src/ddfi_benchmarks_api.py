"""Authenticated caller's bounded industry/country benchmark queries.

Serves DDFI's immutable Damodaran assumption releases (schema
``damodaran-assumptions-v1``, built by data/ddfi ingest/build_assumption_releases.py)
through the Zion private query contract. Mirrors ``sec_expanded_api``: one
integrity-pinned index read, per-release identity verification, bounded inputs,
typed failures, and zero provider calls.

Point-in-time rules (see data/ddfi/docs/QUALITY_AND_CONFORMANCE.md):

* ``published_at`` is the defensible availability date of the served bytes
  (server Last-Modified of the exact source file), never a stated internal
  vintage.
* A historical ``as_of`` selects the newest release published on/before the
  cutoff. If none exists the call fails with structured coverage metadata.
  The API never silently falls back to the newest benchmark.
* Missing and quarantined source cells are served explicitly with markers;
  they are never dropped and never zero-filled.
* Source workbook bytes are never served. Only values, cell coordinates,
  hashes and evidence paths leave this module, and only to the authenticated
  owner tier: rights status ``private_review_pending`` forbids external
  redistribution of the derived reference values.
"""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime

from private_financials import PrivateFinancialsError

INDEX_SCHEMA = "ddfi-benchmark-index-v1"
RELEASE_SCHEMA = "damodaran-assumptions-v1"
EVIDENCE_SCHEMA = "damodaran-publication-evidence-v1"
CLASSIFICATION = "BENCHMARK_ASSUMPTION_NOT_COMPANY_FACT"
PREFIX = "gold/ddfi-benchmarks/v1/"
MAX_INDEX = 262144
MAX_RELEASE_MEMBER = 4 * 1048576
MAX_EVIDENCE = 1048576
MAX_SUBJECTS = 512
# The only reviewed publication-evidence methods; mirrored from the DCF loader so
# both consumers reject the same set instead of drifting apart.
PUBLICATION_METHODS = frozenset({"SERVER_LAST_MODIFIED", "PUBLISHER_STATED", "FIRST_OBSERVED"})

_METADATA_CACHE = {}


def fail(code, message, status=422):
    raise PrivateFinancialsError(code, message, status)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


async def pinned(env, reference, maximum=MAX_RELEASE_MEMBER):
    """Read one hash-pinned object from the benchmark prefix; cache by digest."""
    key, digest = reference["key"], reference["sha256"]
    if (
        not isinstance(key, str)
        or not key.startswith(PREFIX)
        or any(part in ("", ".", "..") for part in key.split("/"))
        or not re.fullmatch(r"[0-9a-f]{64}", str(digest))
    ):
        fail("BENCHMARK_INTEGRITY_FAILURE", "invalid benchmark artifact reference", 503)
    cache_key = (key, digest)
    if cache_key in _METADATA_CACHE:
        return _METADATA_CACHE[cache_key]
    obj = await env.MARKET_DATA.get(key)
    if not obj:
        fail("BENCHMARK_INTEGRITY_FAILURE", "benchmark artifact is unavailable", 503)
    raw = (await obj.text()).encode()
    if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != digest:
        fail("BENCHMARK_INTEGRITY_FAILURE", "benchmark artifact digest mismatch", 503)
    if len(_METADATA_CACHE) >= 32:
        _METADATA_CACHE.pop(next(iter(_METADATA_CACHE)))
    _METADATA_CACHE[cache_key] = raw
    return raw


async def load_index(env):
    key, digest = (
        getattr(env, "DDFI_BENCHMARKS_INDEX_KEY", None),
        getattr(env, "DDFI_BENCHMARKS_INDEX_SHA256", None),
    )
    if not key or not digest:
        fail("BENCHMARKS_NOT_CONFIGURED", "benchmark index is not configured", 503)
    index = json.loads(await pinned(env, {"key": key, "sha256": digest}, maximum=MAX_INDEX))
    if index.get("version") != INDEX_SCHEMA or not isinstance(index.get("releases"), list):
        fail("BENCHMARK_INTEGRITY_FAILURE", "benchmark index schema mismatch", 503)
    return index, digest


async def load_evidence(env):
    """Read the hash-pinned publication-evidence catalog.

    Historical serving fails closed when the catalog is not configured, so code
    can ship before the data/config step without changing current-mode behavior.
    """
    key, digest = (
        getattr(env, "DDFI_BENCHMARKS_EVIDENCE_KEY", None),
        getattr(env, "DDFI_BENCHMARKS_EVIDENCE_SHA256", None),
    )
    if not key or not digest:
        fail("BENCHMARKS_EVIDENCE_NOT_CONFIGURED", "benchmark evidence is not configured", 503)
    catalog = json.loads(await pinned(env, {"key": key, "sha256": digest}, maximum=MAX_EVIDENCE))
    if catalog.get("schema_version") != EVIDENCE_SCHEMA or not isinstance(catalog.get("releases"), list):
        fail("BENCHMARK_INTEGRITY_FAILURE", "benchmark evidence schema mismatch", 503)
    entries = {}
    for item in catalog["releases"]:
        if not isinstance(item, dict) or not isinstance(item.get("release_id"), str):
            fail("BENCHMARK_INTEGRITY_FAILURE", "benchmark evidence entry is malformed", 503)
        if item["release_id"] in entries:
            fail("BENCHMARK_INTEGRITY_FAILURE", "benchmark evidence entries are duplicated", 503)
        entries[item["release_id"]] = item
    return entries, digest


def _instant(value, field):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        fail("BENCHMARK_EVIDENCE_MISMATCH", f"{field} is not an ISO timestamp", 503)
    if parsed.tzinfo is None:
        fail("BENCHMARK_EVIDENCE_MISMATCH", f"{field} requires a timezone", 503)
    return parsed


def _retrieval(value):
    """Return ``(date, instant_or_None)``; a date-only record keeps day precision."""
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text), None
        except ValueError:
            fail("BENCHMARK_EVIDENCE_MISMATCH", "retrieved_at is not a calendar date", 503)
    instant = _instant(value, "retrieved_at")
    return instant.date(), instant


def publication_evidence(entry, manifest, evidence_entries, evidence_digest):
    """Validate the reviewed evidence bound to the *selected* release bytes.

    The release identity chain is already verified by ``_verify_release``; this
    adds the publication claim: it must be recorded for this release, bound to
    the same source digest that the served manifest names, and it must not claim
    a date the evidence could not have supported.
    """
    claim = evidence_entries.get(entry["release_id"])
    if claim is None:
        fail(
            "BENCHMARK_EVIDENCE_MISSING",
            f"release {entry['release_id']} has no reviewed publication evidence",
            503,
        )
    source_sha = manifest.get("files", {}).get("source.bin")
    if not source_sha or claim.get("source_sha256") != source_sha:
        fail("BENCHMARK_EVIDENCE_MISMATCH", "evidence is not bound to the served source bytes", 503)
    record = claim.get("publication_evidence")
    if not isinstance(record, dict):
        fail("BENCHMARK_EVIDENCE_MISMATCH", "publication evidence is not structured", 503)
    published = entry.get("published_at")
    if claim.get("published_at") != published or manifest.get("published_at") != published:
        fail("BENCHMARK_EVIDENCE_MISMATCH", "evidence, index and manifest disagree on publication", 503)
    if record.get("method") not in PUBLICATION_METHODS:
        fail("BENCHMARK_EVIDENCE_MISMATCH", "publication evidence method is not reviewed", 503)
    source_url = str(record.get("source_url") or "")
    if not source_url.startswith("https://") or not record.get("statement"):
        fail("BENCHMARK_EVIDENCE_MISMATCH", "publication evidence is incomplete", 503)
    published_date = date.fromisoformat(published)
    observed = _instant(record.get("observed_at"), "evidence observed_at")
    if observed.date() < published_date:
        fail("BENCHMARK_EVIDENCE_MISMATCH", "evidence precedes publication", 503)
    downloaded = claim.get("download_retrieved_at", claim.get("retrieved_at"))
    if downloaded is None:
        fail("BENCHMARK_EVIDENCE_MISMATCH", "evidence has no recorded download time", 503)
    retrieval_date, retrieval_instant = _retrieval(downloaded)
    if published_date > retrieval_date:
        fail("BENCHMARK_EVIDENCE_MISMATCH", "publication follows the recorded download", 503)
    if record.get("method") == "SERVER_LAST_MODIFIED":
        try:
            modified = parsedate_to_datetime(str(record.get("server_last_modified")))
        except (TypeError, ValueError):
            fail("BENCHMARK_EVIDENCE_MISMATCH", "server Last-Modified is unparseable", 503)
        if modified.tzinfo is None:
            fail("BENCHMARK_EVIDENCE_MISMATCH", "server Last-Modified requires a timezone", 503)
        if modified.date() != published_date:
            fail("BENCHMARK_EVIDENCE_MISMATCH", "published_at is not the server Last-Modified date", 503)
        if retrieval_instant is not None and modified > retrieval_instant:
            fail("BENCHMARK_EVIDENCE_MISMATCH", "server Last-Modified follows the recorded download", 503)
    return {
        "method": record["method"],
        "source_url": source_url,
        "observed_at": observed.isoformat(),
        "server_last_modified": record.get("server_last_modified"),
        "download_precision": "day" if retrieval_instant is None else "instant",
        "catalog_sha256": evidence_digest,
    }


def _as_of_date(value):
    """Accept YYYY-MM-DD or a timezone-aware RFC3339 instant; return its UTC date."""
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2}))?", value
    ):
        fail("INVALID_REQUEST", "as_of must be a date or timezone-aware instant", 400)
    head = value[:10]
    try:
        parsed = date.fromisoformat(head)
    except ValueError:
        fail("INVALID_REQUEST", "as_of is not a valid date", 400)
    if "T" in value or " " in value:
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            fail("INVALID_REQUEST", "as_of is not a valid instant", 400)
        if instant.tzinfo is None:
            fail("INVALID_REQUEST", "as_of instant must be timezone-aware", 400)
        parsed = instant.astimezone(timezone.utc).date()
    if parsed > datetime.now(timezone.utc).date():
        fail("INVALID_REQUEST", "as_of must not be in the future", 400)
    return parsed


def _verify_release(entry, manifest_raw, normalized_raw):
    """Re-verify the consumer identity chain before any observation is served."""
    manifest = json.loads(manifest_raw)
    identity = manifest.get("release_id")
    without = dict(manifest)
    without.pop("release_id", None)
    if (
        identity != entry["release_id"]
        or hashlib.sha256(encoded(without)).hexdigest() != entry["release_id"]
        or manifest.get("schema_version") != RELEASE_SCHEMA
    ):
        fail("BENCHMARK_INTEGRITY_FAILURE", "release identity mismatch", 503)
    if hashlib.sha256(normalized_raw).hexdigest() != manifest.get("files", {}).get("normalized.json"):
        fail("BENCHMARK_INTEGRITY_FAILURE", "normalized digest mismatch", 503)
    data = json.loads(normalized_raw)
    if (
        data.get("schema_version") != RELEASE_SCHEMA
        or data.get("dataset_id") != manifest.get("dataset_id")
        or data.get("region") != manifest.get("region")
    ):
        fail("BENCHMARK_INTEGRITY_FAILURE", "normalized metadata mismatch", 503)
    return manifest, data


async def load_release(env, entry):
    manifest_raw = await pinned(env, entry["manifest"])
    normalized_raw = await pinned(env, entry["normalized"])
    return _verify_release(entry, manifest_raw, normalized_raw)


def _select_release(releases, *, dataset_id, release_id, as_of, expected_release_id):
    """Exact/historical release selection with no silent newest fallback."""
    if release_id is not None:
        entry = next((r for r in releases if r["release_id"] == release_id), None)
        if entry is None:
            fail("BENCHMARK_RELEASE_NOT_FOUND", "requested release is not published", 404)
        if as_of is not None and date.fromisoformat(entry["published_at"]) > as_of:
            fail(
                "BENCHMARK_VINTAGE_UNAVAILABLE",
                f"release {release_id} was published {entry['published_at']}, after the "
                f"{as_of.isoformat()} cutoff; no earlier coverage is published for "
                f"{entry['dataset_id']}",
                404,
            )
        selected = entry
        mode = "EXACT_PINNED" if as_of is None else "EXACT_PINNED_HISTORICAL"
    else:
        candidates = [r for r in releases if r["dataset_id"] == dataset_id]
        if not candidates:
            fail("BENCHMARK_DATASET_NOT_FOUND", f"no published releases for dataset {dataset_id}", 404)
        if as_of is not None:
            eligible = [r for r in candidates if date.fromisoformat(r["published_at"]) <= as_of]
            if not eligible:
                earliest = min(candidates, key=lambda r: r["published_at"])
                vintages = ", ".join(sorted(r["published_at"] for r in candidates)[:6])
                fail(
                    "HISTORICAL_COVERAGE_UNAVAILABLE",
                    f"no {dataset_id} benchmark was published on or before {as_of.isoformat()}; "
                    f"earliest published vintage {earliest['published_at']} "
                    f"(available: {vintages}). Newest-release fallback is forbidden.",
                    404,
                )
            selected = max(eligible, key=lambda r: (r["published_at"], r["release_id"]))
            mode = "HISTORICAL_AS_OF"
        else:
            selected = max(candidates, key=lambda r: (r["published_at"], r["release_id"]))
            mode = "CURRENT_EXPLICIT"
    if expected_release_id is not None and expected_release_id != selected["release_id"]:
        fail(
            "RELEASE_MISMATCH",
            f"expected release {expected_release_id} is not the selected release "
            f"{selected['release_id']}",
            409,
        )
    return selected, mode


def _release_block(entry, mode, index_digest, manifest, evidence=None):
    block = {
        "serving_release_id": "ddfi-benchmarks:" + entry["release_id"],
        "ddfi_release_id": entry["release_id"],
        "dataset_id": entry["dataset_id"],
        "region": entry["region"],
        "subject_type": entry.get("subject_type"),
        "published_at": entry["published_at"],
        "selection_mode": mode,
        "provider": manifest.get("provider"),
        "source_url": manifest.get("source_url"),
        "source_sha256": manifest.get("files", {}).get("source.bin"),
        "normalized_sha256": manifest.get("files", {}).get("normalized.json"),
        "ingested_bundle": manifest.get("lineage", {}).get("ingested_bundle"),
        "retrieved_at": manifest.get("lineage", {}).get("retrieved_at"),
        "stated_publication_date": manifest.get("lineage", {}).get("stated_publication_date"),
        "index_sha256": index_digest,
        "rights": manifest.get("rights", {}),
    }
    if evidence is not None:
        block["publication_evidence"] = evidence
    return block


def _observation(record):
    """Served observation: values, cell coordinates, markers — never source bytes."""
    out = {
        "subject": record["subject"],
        "measure": record["measure"],
        "unit": record["unit"],
        "value_decimal": record["value_decimal"],
        "classification": record["classification"],
        "source_sheet": record.get("source_sheet"),
        "source_column_index": record.get("source_column_index"),
    }
    for field in (
        "subject_type", "currency", "missing", "missing_reason", "quarantined",
        "workbook_error", "workbook_text", "derived", "derivation", "subject_region",
    ):
        if field in record:
            out[field] = record[field]
    return out


async def query_benchmarks(env, request: dict, *, request_id: str) -> dict:
    try:
        return await _query(env, request, request_id)
    except PrivateFinancialsError:
        raise
    except Exception as exc:
        import traceback

        frames = traceback.extract_tb(exc.__traceback__)
        print("ddfi_benchmark_failure", type(exc).__name__, [(f.name, f.lineno) for f in frames])
        fail("BENCHMARK_DATA_UNAVAILABLE", "benchmark source data is unavailable", 503)


async def _query(env, request, request_id):
    if not isinstance(request, dict):
        fail("INVALID_REQUEST", "a JSON object is required", 400)
    operation = request.get("operation")
    if operation == "benchmark_discovery":
        allowed = {"operation", "dataset_id", "include_subjects"}
    elif operation == "benchmark_context":
        allowed = {
            "operation", "dataset_id", "release_id", "subject", "measures",
            "as_of", "expected_release_id",
        }
    else:
        fail("INVALID_REQUEST", "unsupported benchmark operation", 400)
    if set(request) - allowed:
        fail("INVALID_REQUEST", "unknown benchmark argument", 400)
    index, index_digest = await load_index(env)
    releases = index["releases"]
    for entry in releases:
        for field in ("release_id", "dataset_id", "region", "published_at", "manifest", "normalized"):
            if not isinstance(entry.get(field), (str, dict)):
                fail("BENCHMARK_INTEGRITY_FAILURE", "benchmark index entry is malformed", 503)

    dataset_id = request.get("dataset_id")
    if dataset_id is not None and (
        not isinstance(dataset_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", dataset_id)
    ):
        fail("INVALID_REQUEST", "dataset_id must be a bounded slug", 400)
    release_id = request.get("release_id")
    if release_id is not None and (
        not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{64}", release_id)
    ):
        fail("INVALID_REQUEST", "release_id must be a 64-hex release identity", 400)

    if operation == "benchmark_discovery":
        if dataset_id is None and release_id is not None:
            entry = next((r for r in releases if r["release_id"] == release_id), None)
            if entry is None:
                fail("BENCHMARK_RELEASE_NOT_FOUND", "requested release is not published", 404)
            selected = [entry]
        elif dataset_id is not None:
            selected = [r for r in releases if r["dataset_id"] == dataset_id]
            if not selected:
                fail("BENCHMARK_DATASET_NOT_FOUND", f"no published releases for dataset {dataset_id}", 404)
        else:
            selected = releases
        include_subjects = request.get("include_subjects", False)
        if not isinstance(include_subjects, bool):
            fail("INVALID_REQUEST", "include_subjects must be a boolean", 400)
        datasets = []
        for entry in sorted(selected, key=lambda r: (r["dataset_id"], r["published_at"])):
            item = {
                "release_id": entry["release_id"],
                "dataset_id": entry["dataset_id"],
                "region": entry["region"],
                "subject_type": entry.get("subject_type"),
                "published_at": entry["published_at"],
                "stated_publication_date": entry.get("stated_publication_date"),
                "measures": entry.get("measures", []),
                "record_count": entry.get("record_count"),
                "missing_count": entry.get("missing_count"),
                "rights_status": entry.get("rights_status"),
                "serving_release_id": "ddfi-benchmarks:" + entry["release_id"],
            }
            if include_subjects:
                _manifest, data = await load_release(env, entry)
                subjects = sorted({r["subject"] for r in data["records"]})
                if len(subjects) > MAX_SUBJECTS:
                    fail("BENCHMARK_RESPONSE_TOO_LARGE", "dataset exceeds subject listing bounds", 413)
                item["subjects"] = subjects
                item["definitions"] = data.get("definitions", {})
                item["unit_definitions"] = data.get("unit_definitions", {})
            datasets.append(item)
        return {
            "schema_version": "zion-private-query-v1",
            "request_id": request_id,
            "operation": operation,
            "release": {
                "serving_release_id": "ddfi-benchmarks-index",
                "index_sha256": index_digest,
                "provider": index.get("provider"),
                "rights_status": index.get("rights_status"),
            },
            "data": {"datasets": datasets, "guardrail": index.get("guardrail")},
            "coverage": {
                "status": "AVAILABLE",
                "live_provider_calls": 0,
                "source_bytes_served": False,
                "external_redistribution": "PROHIBITED_PENDING_REVIEW",
                "independent_validation": "SOURCE_CELL_PRESERVED",
            },
        }

    # benchmark_context
    if bool(dataset_id) == bool(release_id):
        fail("INVALID_REQUEST", "specify exactly one dataset_id or release_id", 400)
    subject = request.get("subject")
    if not isinstance(subject, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9 ()&/.,+'-]{0,78}", subject
    ):
        fail("INVALID_REQUEST", "a bounded benchmark subject is required", 400)
    measures = request.get("measures")
    if measures is not None and (
        not isinstance(measures, list)
        or not 1 <= len(measures) <= 20
        or any(
            not isinstance(m, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", m)
            for m in measures
        )
        or len(set(measures)) != len(measures)
    ):
        fail("INVALID_REQUEST", "measures must contain 1 to 20 distinct measure names", 400)
    as_of = _as_of_date(request["as_of"]) if "as_of" in request else None
    expected = request.get("expected_release_id")
    if expected is not None and (
        not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        fail("INVALID_REQUEST", "expected_release_id must be a release identity", 400)

    entry, mode = _select_release(
        releases,
        dataset_id=dataset_id,
        release_id=release_id,
        as_of=as_of,
        expected_release_id=expected,
    )
    manifest, data = await load_release(env, entry)
    # Publication evidence gates *historical* serving only. Current selection is
    # an explicit choice of the newest release and keeps its existing contract.
    provenance = None
    if as_of is not None:
        evidence_entries, evidence_digest = await load_evidence(env)
        provenance = publication_evidence(entry, manifest, evidence_entries, evidence_digest)
    known = {r["measure"] for r in data["records"]}
    if measures is not None:
        unknown = sorted(set(measures) - known)
        if unknown:
            fail(
                "BENCHMARK_MEASURE_UNKNOWN",
                f"dataset {entry['dataset_id']} has no measure {unknown[0]}; "
                f"available measures include {', '.join(sorted(known)[:8])}",
                404,
            )
        wanted = set(measures)
    else:
        wanted = known
    selected = [r for r in data["records"] if r["subject"] == subject and r["measure"] in wanted]
    if not selected:
        sample = sorted({r["subject"] for r in data["records"]})[:6]
        fail(
            "BENCHMARK_SUBJECT_NOT_FOUND",
            f"subject {subject!r} is not in dataset {entry['dataset_id']} "
            f"(subjects include {', '.join(sample)})",
            404,
        )
    observations = [_observation(r) for r in sorted(selected, key=lambda r: r["measure"])]
    missing = [o for o in observations if o["value_decimal"] is None]
    return {
        "schema_version": "zion-private-query-v1",
        "request_id": request_id,
        "operation": operation,
        "release": _release_block(entry, mode, index_digest, manifest, provenance),
        "data": {
            "subject": subject,
            "subject_type": data.get("subject_type"),
            "dataset_id": entry["dataset_id"],
            "region": entry["region"],
            "observations": observations,
            "definitions": data.get("definitions", {}),
            "unit_definitions": data.get("unit_definitions", {}),
            "measurement_period_note": data.get("measurement_period_note"),
            "missing_count": len(missing),
            "missing_measures": sorted(o["measure"] for o in missing),
            "guardrail": (
                "Industry/country benchmark reference estimates. NOT issuer facts, "
                "NOT live market data; classification is preserved on every observation."
            ),
        },
        "coverage": {
            "status": "PARTIAL" if missing else "AVAILABLE",
            "as_of": as_of.isoformat() if as_of is not None else None,
            "historical_policy": (
                "POINT_IN_TIME_PUBLISHED; historical cutoffs never fall back to the "
                "newest benchmark; unavailable coverage fails closed"
            ),
            "live_provider_calls": 0,
            "source_sha256": manifest.get("files", {}).get("source.bin"),
            "evidence_path": manifest.get("lineage", {}).get("evidence_path"),
            "source_bytes_served": False,
            "rights_status": manifest.get("rights", {}).get("rights_status"),
            "external_redistribution": manifest.get("rights", {}).get("redistribution_policy"),
            "independent_validation": "SOURCE_CELL_PRESERVED; CONSUMER_NUMERICS_TESTED",
            "publication_evidence": (
                "REVIEWED_CATALOG_BOUND_TO_SERVED_SOURCE_BYTES"
                if provenance is not None
                else "NOT_APPLICABLE_CURRENT_SELECTION"
            ),
        },
    }
