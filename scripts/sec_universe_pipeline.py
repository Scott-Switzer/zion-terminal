"""Checkpointed, independently reconciled SEC archive streaming; bounded disk/spend.

Only public SEC members are processed. Original archives/CURRENT remain untouched.
Company checkpoints persist after independent reconciliation and cloud readback.
"""

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import zlib
from collections import Counter, OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).parent))
from fractions import Fraction

import materialize_sec_engine as materializer
from reconcile_financial_inventory import client
from sec_engine import REGISTRY, encoded, history
from sec_screener import company_row, exact

BASE = materializer.OUT
OUT = BASE.parent / "sec-universe-sprint"
BUCKET = "financial-system-datasets"


class ResourceStop(RuntimeError):
    pass


class Budget:
    def __init__(self, dollars, maximum_bytes=20_000_000_000):
        self.dollars = dollars
        self.maximum_bytes = maximum_bytes
        self.bytes = 0
        self.writes = 0
        self.lock = Lock()

    def reserve_write(self, size):
        if self.bytes + size > self.maximum_bytes:
            raise ResourceStop("STORAGE_BUDGET")
        # Reserve one whole paid Class A unit plus retained first-month storage.
        # Reads (<1M) reserve one whole Class B unit; excluded ongoing base plans.
        ceiling = 4.50 + 0.36 + math.ceil((self.bytes + size) / 1e9) * 0.015
        if self.writes + 1 > 1_000_000 or ceiling > self.dollars:
            raise ResourceStop("SPEND_BUDGET")
        self.bytes += size
        self.writes += 1


def expansion_stages(total):
    return sorted(set(min(total, n) for n in (2000, 5000, total)))


def save_checkpoint(path, value):
    raw = gzip.compress(encoded(value), mtime=0)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


def load_checkpoint(path, context):
    value = json.loads(gzip.decompress(path.read_bytes()))
    if value["context"] != context:
        raise ValueError("CHECKPOINT_CONTEXT")
    return value


def pin(s, prefix, name, raw, budget, reserve=True):
    if reserve:
        with budget.lock:
            budget.reserve_write(len(raw))
    key = prefix + name
    try:
        s.put_object(
            Bucket=BUCKET, Key=key, Body=raw, ContentType="application/json", IfNoneMatch="*"
        )
    except s.exceptions.ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
            raise
    response = s.get_object(Bucket=BUCKET, Key=key)
    try:
        received = response["Body"].read(len(raw) + 1)
    finally:
        response["Body"].close()
    if received != raw:
        raise ValueError("PUBLICATION_READBACK")
    return {"key": key, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


class CachedRangeArchive:
    """Three 4MiB blocks, no archive disk copy; every block pinned to source ETag."""

    def __init__(self, s, source):
        self.s, self.source = s, source
        self.blocks = OrderedDict()
        self.bytes_read = 0
        self.requests = 0

    def get_object(self, **kwargs):
        start, end = map(int, kwargs["Range"].removeprefix("bytes=").split("-"))
        parts = []
        for block in range(start // (4 * 1048576), end // (4 * 1048576) + 1):
            low = block * 4 * 1048576
            high = min(low + 4 * 1048576, self.source["bytes"])
            if block not in self.blocks:
                r = self.s.get_object(
                    Bucket=self.source["bucket"],
                    Key=self.source["key"],
                    Range=f"bytes={low}-{high - 1}",
                    IfMatch=self.source["etag"],
                )
                try:
                    raw = r["Body"].read(high - low + 1)
                finally:
                    r["Body"].close()
                if len(raw) != high - low:
                    raise ValueError("ARCHIVE_BLOCK_LENGTH")
                self.blocks[block] = raw
                self.bytes_read += len(raw)
                self.requests += 1
                if len(self.blocks) > 3:
                    self.blocks.popitem(last=False)
            self.blocks.move_to_end(block)
            parts.append(self.blocks[block][max(start - low, 0) : min(end - low + 1, high - low)])
        return {"Body": io.BytesIO(b"".join(parts))}


def classify_source(raw, cik):
    """Classify reporting metadata only; never convert these facts to financial values."""
    try:
        source = json.loads(raw)
        if str(source.get("cik", "")).zfill(10) != cik:
            return {"eligible": False, "classification": "SOURCE_IDENTITY_MISMATCH"}
        definitions = source.get("facts", {}).get("us-gaap", {})
        if not definitions:
            return {"eligible": False, "classification": "NO_US_GAAP_TAXONOMY"}
        periodic = any(
            row.get("form") in ("10-K", "10-K/A", "10-Q", "10-Q/A")
            for definition in definitions.values()
            for rows in definition.get("units", {}).values()
            for row in rows
        )
        return {
            "eligible": periodic,
            "classification": "ELIGIBLE_US_PERIODIC_SOURCE"
            if periodic
            else "NO_US_PERIODIC_CONTEXTS",
        }
    except (ValueError, TypeError, AttributeError):
        return {"eligible": None, "classification": "SOURCE_SCHEMA_UNVERIFIED"}


def evaluate(raw, cik, source, entry, release, registry_sha, temporary):
    data, error = materializer.evaluate_member(raw, cik, source, entry, release, registry_sha)
    if error:
        return {
            "status": "QUARANTINED",
            "reason": error,
            "member_sha256": materializer.digest(raw),
            "source_eligibility": classify_source(raw, cik),
        }
    body = encoded(data)
    if len(body) > 16 * 1048576 or len(data["observations"]) > 12000:
        return {
            "status": "QUARANTINED",
            "reason": "MATERIALIZED_SIZE_BUDGET",
            "member_sha256": materializer.digest(raw),
        }
    (temporary / (cik + ".companyfacts.json")).write_bytes(raw)
    (temporary / (cik + ".history.json")).write_bytes(body)
    counts = {}
    annual = None
    for frequency in ("annual", "quarterly", "ttm"):
        result = history(
            data,
            metrics=list(REGISTRY),
            frequency=frequency,
            date_from="1930-01-01",
            date_to=source["snapshot_date"],
            as_of=source["snapshot_date"] + "T00:00:00Z",
        )
        result.update(source_release_id=release, registry_sha256=registry_sha)
        (temporary / (cik + "." + frequency + ".expected.json")).write_bytes(encoded(result))
        counts[frequency] = {
            metric: {
                "observations": sum(r["metric"] == metric for r in result["observations"]),
                "gaps": sum(r["metric"] == metric for r in result["coverage_gaps"]),
            }
            for metric in REGISTRY
        }
        if frequency == "annual":
            annual = result
    if not any(v["observations"] for frequency in counts.values() for v in frequency.values()):
        return {
            "status": "QUARANTINED",
            "reason": "NO_VALID_MAPPED_FINANCIAL_HISTORY",
            "member_sha256": data["member_sha256"],
            "source_eligibility": classify_source(raw, cik),
        }
    ref = {
        "sha256": materializer.digest(body),
        "bytes": len(body),
        "label": data["label"],
        "member_sha256": data["member_sha256"],
        "policy": data["policy"],
        "normalized_revisions": len(data["observations"]),
        "coverage": counts,
        "source_inventory": {k: v for k, v in data["source_inventory"].items() if k != "concepts"},
    }
    (temporary / "engine-manifest.json").write_bytes(
        encoded({"release_id": release, "registry_sha256": registry_sha, "companies": {cik: ref}})
    )
    import verify_sec_engine_independent as independent

    independent.OUT = temporary
    with contextlib.redirect_stdout(io.StringIO()):
        independent.main()
    verification = json.loads((temporary / "independent-reconciliation.json").read_text())
    assert verification["status"] == "PASS"
    return {
        "status": "USABLE",
        "source_eligibility": {"eligible": True, "classification": "ELIGIBLE_US_PERIODIC_SOURCE"},
        "ref": ref,
        "body": body,
        "screen": company_row(cik, data, annual["observations"]),
        "verification": {k: v for k, v in verification.items() if k != "checks"},
    }


def load_stages(directory, total):
    stages = [
        json.loads(path.read_text())
        for path in sorted(directory.glob("stage-*.json"))
        if re.fullmatch(r"stage-\d+\.json", path.name)
    ]
    if not all(
        stage.get("status") == "PASS" and stage.get("attempted") in expansion_stages(total)
        for stage in stages
    ):
        raise ValueError("STAGE_RECEIPT_INVALID")
    return stages


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--maximum-spend", type=float, required=True)
    p.add_argument("--total", type=int, default=20396)
    p.add_argument("--maximum-seconds", type=int, default=10800)
    a = p.parse_args()
    if not 0 < a.maximum_spend <= 5 or not 1 <= a.total <= 20396:
        raise ValueError("AUTHORIZED_RESOURCE_LIMIT")
    OUT.mkdir(parents=True, exist_ok=True)
    checkpoints = OUT / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    base = json.loads((BASE / "engine-manifest.json").read_text())
    entries = json.loads((BASE / "full-member-index.json").read_text())
    registry_raw = (BASE / "registry.json").read_bytes()
    registry = json.loads(registry_raw)
    for name, sha in {
        "sec_engine.py": registry["implementation_sha256"],
        **registry["dependency_sha256"],
    }.items():
        assert (
            materializer.digest(
                (Path(__file__).parents[1] / "deploy/cloudflare-query/src" / name).read_bytes()
            )
            == sha
        )
    selected = list(base["companies"]) + list(base["quarantined"])
    selected = list(dict.fromkeys(selected + [x for x in sorted(entries) if x not in selected]))[
        : a.total
    ]
    release = materializer.release_identity(
        base["source"], base["registry_sha256"], entries, selected
    )
    prefix = "gold/sec-engine/releases/" + release + "/"
    budget = Budget(a.maximum_spend, maximum_bytes=9_000_000_000)
    s = client()
    local = CachedRangeArchive(s, base["source"])
    started = time.perf_counter()
    stages = load_stages(OUT, a.total)
    reason = None
    pool = ThreadPoolExecutor(max_workers=4)
    pending = deque()

    def finish(cik, value, checkpoint):
        if value["status"] == "USABLE":
            body = value.pop("body")
            value["ref"].update(
                pin(s, prefix, "companies/" + cik + ".json", body, budget, reserve=False)
            )
        value.update(context=release, cik=cik)
        save_checkpoint(checkpoint, value)

    prior_verification = json.loads((BASE / "independent-reconciliation.json").read_text())
    assert (
        prior_verification["status"] == "PASS"
        and prior_verification["release_id"] == base["release_id"]
        and prior_verification["registry_sha256"] == base["registry_sha256"]
    )
    prior_counts = Counter()
    for check in prior_verification["checks"]:
        assert check["status"] == "PASS"
        prior_counts[check["cik"]] += check["observations_independently_reconciled"]
    for index, cik in enumerate(selected):
        checkpoint = checkpoints / (cik + ".json.gz")
        if checkpoint.exists():
            old = load_checkpoint(checkpoint, release)
            if old["status"] == "USABLE":
                budget.reserve_write(old["ref"]["bytes"])
            continue
        if (
            time.perf_counter() - started > a.maximum_seconds
            or shutil.disk_usage(OUT).free < 200 * 1048576
        ):
            reason = "ELAPSED_OR_DISK_BUDGET"
            break
        entry = entries[cik]
        try:
            if cik in base["companies"]:
                ref = dict(base["companies"][cik])
                body = (BASE / (cik + ".history.json")).read_bytes()
                assert materializer.digest(body) == ref["sha256"]
                data = json.loads(body)
                data["release_id"] = release
                assert len([c for c in prior_verification["checks"] if c["cik"] == cik]) == 3
                annual = history(
                    data,
                    metrics=list(REGISTRY),
                    frequency="annual",
                    date_from="1930-01-01",
                    date_to=base["date_to"],
                    as_of=base["maximum_as_of"],
                )
                value = {
                    "status": "USABLE",
                    "ref": ref,
                    "body": encoded(data),
                    "screen": company_row(cik, data, annual["observations"]),
                    "verification": {
                        "status": "PASS",
                        "total_observations": prior_counts[cik],
                        "reused_evidence_release": base["release_id"],
                    },
                }
            else:
                raw = materializer.extract(local, base["source"], entry)
                with tempfile.TemporaryDirectory(prefix="issuer-", dir=OUT) as temporary:
                    value = evaluate(
                        raw,
                        cik,
                        base["source"],
                        entry,
                        release,
                        base["registry_sha256"],
                        Path(temporary),
                    )
        except ResourceStop as error:
            reason = str(error)
            break
        except OSError as error:
            if error.errno != 28:
                raise
            reason = "LOCAL_DISK_EXHAUSTED_DURING_ISSUER"
            break
        except (AssertionError, ValueError, KeyError, TypeError, zlib.error) as error:
            value = {
                "status": "QUARANTINED",
                "reason": "SOURCE_OR_RECONCILIATION_FAILURE",
                "error_type": type(error).__name__,
                "detail": str(error)[:160],
            }
        if value["status"] == "USABLE":
            if budget.bytes + len(value["body"]) > 7_000_000_000:
                reason = "AUTHORIZED_FIRST_MONTH_SPEND_RESERVE"
                break
            budget.reserve_write(len(value["body"]))
        pending.append(pool.submit(finish, cik, value, checkpoint))
        if len(pending) >= 4:
            pending.popleft().result()
        if (index + 1) % 100 == 0:
            print(
                json.dumps(
                    {
                        "attempted": index + 1,
                        "elapsed_seconds": round(time.perf_counter() - started, 2),
                        "published_bytes": budget.bytes,
                    }
                ),
                flush=True,
            )
        if index + 1 in expansion_stages(a.total):
            while pending:
                pending.popleft().result()
            stage = {
                "attempted": index + 1,
                "status": "PASS",
                "elapsed_seconds": round(time.perf_counter() - started, 2),
                "published_bytes": budget.bytes,
            }
            stages = [old for old in stages if old["attempted"] != stage["attempted"]] + [stage]
            (OUT / ("stage-" + str(index + 1) + ".json")).write_bytes(encoded(stage))
    while pending:
        pending.popleft().result()
    pool.shutdown(wait=True)
    records = {
        path.name.split(".")[0]: load_checkpoint(path, release)
        for path in checkpoints.glob("*.json.gz")
    }
    for cik, record in sorted(records.items()):
        if record.get("source_eligibility") is not None:
            continue
        if record["status"] == "USABLE":
            eligibility = {"eligible": True, "classification": "ELIGIBLE_US_PERIODIC_SOURCE"}
        elif record.get("reason") == "SOURCE_IDENTITY_MISMATCH":
            eligibility = {"eligible": False, "classification": "SOURCE_IDENTITY_MISMATCH"}
        else:
            try:
                eligibility = classify_source(
                    materializer.extract(local, base["source"], entries[cik]), cik
                )
            except (ValueError, TypeError, zlib.error):
                eligibility = {
                    "eligible": None,
                    "classification": "SOURCE_INTEGRITY_OR_BUDGET_UNVERIFIED",
                }
        record["source_eligibility"] = eligibility
        save_checkpoint(checkpoints / (cik + ".json.gz"), record)
    resources = {
        "range_requests_this_invocation": local.requests,
        "source_bytes_this_invocation": local.bytes_read,
        "range_cache_maximum_bytes": 3 * 4 * 1048576,
        "financial_calculator_threads": 1,
        "publication_io_threads": 4,
    }
    (OUT / "resource-usage.json").write_bytes(encoded(resources))
    publish_catalog(
        s, base, entries, registry_raw, release, prefix, records, budget, reason, stages, started
    )


def publication_identity(run_id, records):
    return materializer.digest(encoded({"run_id": run_id, "checkpoints": records}))


def publish_catalog(
    s, base, entries, registry_raw, release, prefix, records, budget, reason, stages, started
):
    materialization_release = release
    release = publication_identity(release, records)
    prefix = "gold/sec-engine/releases/" + release + "/"
    companies = {cik: r["ref"] for cik, r in records.items() if r["status"] == "USABLE"}
    quarantined = {
        cik: {k: v for k, v in r.items() if k not in ("cik", "context", "status")}
        for cik, r in records.items()
        if r["status"] != "USABLE"
    }
    root = {
        k: v for k, v in base.items() if k not in ("companies", "quarantined", "screening_index")
    }
    prior_publication = json.loads((BASE / "publication.json").read_text())
    assert prior_publication["release_id"] == base["release_id"]
    root["previous_releases"] = {base["release_id"]: prior_publication["root"]}
    root.update(
        release_id=release,
        materialized_companies=len(companies),
        quarantined_issuers=len(quarantined),
        issuers_attempted=len(records),
        companies_fully_certified=0,
        source_releases=[materialization_release],
        source_eligibility_counts=dict(
            Counter(
                "eligible"
                if r.get("source_eligibility", {}).get("eligible") is True
                else "ineligible"
                if r.get("source_eligibility", {}).get("eligible") is False
                else "unverified"
                for r in records.values()
            )
        ),
    )
    shards = {}
    directory = []
    for cik, entry in entries.items():
        ref = companies.get(cik)
        item = {
            "archive_entry": entry,
            "label": ref["label"] if ref else None,
            "source_eligibility": records.get(cik, {}).get("source_eligibility"),
        }
        if ref:
            item.update(
                materialized={
                    **{k: ref[k] for k in ("key", "sha256", "bytes", "member_sha256")},
                    "source_release_id": materialization_release,
                },
                coverage=ref["coverage"],
                policy=ref["policy"],
            )
        if cik in quarantined:
            item["quarantine"] = quarantined[cik]
        shards.setdefault(cik[-2:], {})[cik] = item
        directory.append(
            {
                "cik": cik,
                "label": item["label"],
                "status": "MATERIALIZED"
                if ref
                else "QUARANTINED"
                if cik in quarantined
                else "ELIGIBILITY_NOT_YET_VERIFIED",
                "quarantine_reason": quarantined.get(cik, {}).get("reason"),
            }
        )
    root["shards"] = {
        suffix: pin(s, prefix, "catalog/" + suffix + ".json", encoded(value), budget)
        for suffix, value in sorted(shards.items())
    }
    root["directory"] = pin(
        s, prefix, "directory.json", encoded(sorted(directory, key=lambda x: x["cik"])), budget
    )
    root["registry"] = pin(s, prefix, "registry.json", registry_raw, budget)
    # Metric-specific indexes preserve full provenance while bounding per-query reads.
    screen_refs = {}
    for metric in sorted(set(REGISTRY) | {"revenue_growth"}):
        rows = []
        for cik in sorted(companies):
            row = records[cik]["screen"]
            rows.append(
                {
                    **{k: v for k, v in row.items() if k not in ("metrics", "missing")},
                    "metrics": {metric: row["metrics"][metric]} if metric in row["metrics"] else {},
                    "missing": {metric: row["missing"].get(metric)},
                }
            )

        def ordering(row):
            value = row["metrics"].get(metric)
            number = (
                (
                    Fraction(int(value["numerator"]), int(value["denominator"]))
                    if metric == "revenue_growth"
                    else exact(value["value_decimal"])
                )
                if value is not None
                else Fraction(0)
            )
            return (value is None, number, row["cik"])

        rows.sort(key=ordering)
        chunks = []
        chunk = []
        size = 2
        for row in rows:
            length = len(encoded(row)) + (1 if chunk else 0)
            if chunk and size + length > 2 * 1048576 - 1024:
                chunks.append(chunk)
                chunk = []
                size = 2
                length -= 1
            chunk.append(row)
            size += length
        if chunk:
            chunks.append(chunk)
        screen_refs[metric] = [
            pin(
                s,
                prefix,
                "screen/" + metric + "/" + str(i) + ".json",
                encoded(
                    {
                        "companies": chunk,
                        "release_id": release,
                        "registry_sha256": base["registry_sha256"],
                    }
                ),
                budget,
            )
            for i, chunk in enumerate(chunks)
        ]
    root["screening_metrics"] = {
        "schema_version": "sec-screen-metric-shards-v1",
        "metrics": screen_refs,
        "qualification": (
            "LATEST_VALID_ANNUAL_PER_METRIC;FISCAL_ENDS_MAY_DIFFER;NOT_FULL_CERTIFICATION"
        ),
    }
    for name in ("sec_engine.py", "sec_screener.py", "temporal_core.py", "new_york_tz.py"):
        pin(
            s,
            prefix,
            "implementation/" + name,
            (Path(__file__).parents[1] / "deploy/cloudflare-query/src" / name).read_bytes(),
            budget,
        )
    rootref = pin(s, prefix, "manifest.json", encoded(root), budget)
    receipt = {
        "release_id": release,
        "root": rootref,
        "discovered": len(entries),
        "attempted": len(records),
        "usable": len(companies),
        "quarantined": len(quarantined),
        "quarantine_reasons": dict(Counter(x["reason"] for x in quarantined.values())),
        "normalized_revisions": sum(x["normalized_revisions"] for x in companies.values()),
        "independently_reconciled_outputs": sum(
            r.get("verification", {}).get("total_observations", 0) for r in records.values()
        ),
        "fully_certified_companies": 0,
        "source_eligibility_counts": root["source_eligibility_counts"],
        "source_eligibility_reasons": dict(
            Counter(
                r.get("source_eligibility", {}).get("classification", "NOT_CLASSIFIED")
                for r in records.values()
            )
        ),
        "stages": stages,
        "resource_stop": reason,
        "cloud_bytes_written": budget.bytes,
        "cloud_create_calls": budget.writes,
        "authorized_spend_usd": budget.dollars,
        "estimated_first_month_ceiling_usd": round(
            4.5 + 0.36 + math.ceil(budget.bytes / 1e9) * 0.015, 4
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "current_pointer_writes": 0,
        "licensed_sources": 0,
        "source_range_reads": getattr(s, "range_reads", None),
    }
    (OUT / "serving-manifest.json").write_bytes(encoded(root))
    (OUT / "publication.json").write_bytes(encoded(receipt))
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
