"""Resumable official-source acquisition and immutable historical-series releases.

No provider credentials, estimates, production pointers or availability inference.
Historical source snapshots remain retrospective until vintage evidence is supplied.
"""

import hashlib
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import requests

SCHEMA = "zion-historical-series-v1"
PREFIX = "gold/historical-series/releases/"
MAX_RAW = 32 * 1048576
MAX_OBJECT = 1048576
HOSTS = {"home.treasury.gov", "www.ecb.europa.eu", "thedocs.worldbank.org"}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def download(url):
    """Bounded HTTPS, explicit official origins, no secret-bearing sessions or redirects."""
    for _ in range(4):
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Source origin refused")
        with requests.get(
            url,
            stream=True,
            timeout=(15, 60),
            allow_redirects=False,
            headers={"User-Agent": "ZionHistoricalResearch/1.0"},
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                from urllib.parse import urljoin

                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            parts = []
            size = 0
            for part in response.iter_content(65536):
                size += len(part)
                if size > MAX_RAW:
                    raise ValueError("Source byte budget exceeded")
                parts.append(part)
            raw = b"".join(parts)
            if not raw:
                raise ValueError("Empty source")
            return dict(
                raw=raw,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
    raise ValueError("Source redirect budget exceeded")


def acquire(tasks, adapters, directory, *, fetcher=None, workers=3, refresh=False):
    """Independent tasks checkpoint only after full parse; verified reuse makes zero HTTP calls."""
    if not 1 <= workers <= 4 or len(tasks) > 160:
        raise ValueError("Acquisition budget exceeded")
    if len({task["id"] for task in tasks}) != len(tasks):
        raise ValueError("Duplicate acquisition task")
    directory = Path(directory)
    fetcher = fetcher or download
    (directory / "raw").mkdir(parents=True, exist_ok=True)
    (directory / "checkpoints").mkdir(parents=True, exist_ok=True)

    def one(task):
        try:
            checkpoint = directory / "checkpoints" / (digest(encoded(task)) + ".json")
            record = None
            if checkpoint.exists() and not refresh:
                try:
                    candidate = json.loads(checkpoint.read_bytes())
                    pin = candidate["source_sha256"]
                    if not re.fullmatch(r"[a-f0-9]{64}", pin):
                        raise ValueError()
                    raw = (directory / "raw" / (pin + ".bin")).read_bytes()
                    if len(raw) <= MAX_RAW and digest(raw) == pin and candidate["task"] == task:
                        record = candidate
                except (ValueError, KeyError, OSError):
                    record = None
            cached = record is not None
            if not cached:
                fetched = fetcher(task["url"])
                raw = fetched["raw"]
                if not raw or len(raw) > MAX_RAW:
                    raise ValueError("Source byte budget exceeded")
                record = dict(
                    task=task,
                    source_sha256=digest(raw),
                    bytes=len(raw),
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    etag=fetched.get("etag"),
                    last_modified=fetched.get("last_modified"),
                )
            observations = adapters[task["dataset"]].parse(raw, task)
            if not observations:
                raise ValueError("Empty normalized source")
            for row in observations:
                row["source_id"] = task["id"]
            if not cached:
                atomic(directory / "raw" / (record["source_sha256"] + ".bin"), raw)
                atomic(checkpoint, encoded(record))
            return dict(observations=observations, source=record, cached=cached)
        except Exception as error:
            # No upstream response, exception message, credential or caller URL in receipts.
            return dict(
                failure=dict(
                    task_id=task["id"], dataset=task["dataset"], error=type(error).__name__
                )
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(one, tasks))
    sources = {}
    observations = []
    failures = []
    for outcome in outcomes:
        if "failure" in outcome:
            failures.append(outcome["failure"])
            continue
        source = outcome["source"]
        task = source["task"]
        sources[task["id"]] = dict(source, url=task["url"], dataset=task["dataset"])
        observations.extend(outcome["observations"])
    return dict(
        observations=observations,
        sources=sources,
        failures=failures,
        fetched_tasks=sum(not r["cached"] for r in outcomes if "cached" in r),
        cached_tasks=sum(r["cached"] for r in outcomes if "cached" in r),
    )


def build_release(observations, sources, directory, *, discovery=None):
    """One compact, verified object per series; all reported nulls and source references survive."""
    grouped = defaultdict(dict)
    metadata = {}
    for row in observations:
        series = row["series_id"]
        day = row["observation_date"]
        value = row["value_decimal"]
        if (
            not re.fullmatch(r"[A-Za-z0-9_:.-]{1,180}", series)
            or date.fromisoformat(day).isoformat() != day
            or row["source_id"] not in sources
        ):
            raise ValueError("Invalid series identity or provenance")
        if value is not None and (not isinstance(value, str) or not Decimal(value).is_finite()):
            raise ValueError("Invalid numeric observation")
        if value is None and not row.get("missing_reason"):
            raise ValueError("Missing reason required")
        spec = {key: row[key] for key in ("unit", "asset_class", "label")}
        spec["frequency"] = row.get("frequency", "daily")
        if series in metadata and metadata[series] != spec:
            raise ValueError("Series unit or frequency conflict")
        metadata[series] = spec
        item = [day, value, row.get("missing_reason"), row["source_id"]]
        if day in grouped[series] and grouped[series][day] != item:
            raise ValueError("Observation conflict")
        grouped[series][day] = item
    bodies = {}
    entries = {}
    for series, items in sorted(grouped.items()):
        rows = [items[day] for day in sorted(items)]
        body = encoded(
            dict(
                schema_version=SCHEMA,
                series_id=series,
                columns=["observation_date", "value_decimal", "missing_reason", "source_id"],
                rows=rows,
            )
        )
        if len(body) > MAX_OBJECT:
            raise ValueError("Series object exceeds serving budget")
        name = "series/" + digest(series.encode()) + ".json"
        bodies[name] = body
        entries[series] = dict(
            metadata[series],
            file=name,
            sha256=digest(body),
            bytes=len(body),
            date_min=rows[0][0],
            date_max=rows[-1][0],
            rows=len(rows),
            observations=sum(r[1] is not None for r in rows),
            missing=sum(r[1] is None for r in rows),
        )
    if not entries:
        raise ValueError("Empty release")
    identity = dict(
        schema_version=SCHEMA,
        series=entries,
        sources=sources,
        discovery=discovery or [],
        point_in_time_status="NOT_ESTABLISHED",
        historical_as_of_supported=False,
        available_at=None,
        availability_policy="OBSERVATION_DATE_IS_NOT_PUBLICATION_DATE",
        rights="OWNER_PRIVATE_SOURCE_RESEARCH;REDISTRIBUTION_REQUIRES_SOURCE_TERMS_REVIEW",
    )
    release = digest(encoded(identity))
    prefix = PREFIX + release + "/"
    manifest = dict(
        identity,
        release_id=release,
        series_count=len(entries),
        observation_count=sum(e["observations"] for e in entries.values()),
        missing_count=sum(e["missing"] for e in entries.values()),
    )
    # Local paths are deterministic derivatives, never accepted as remote request arguments.
    for series, entry in manifest["series"].items():
        entry["key"] = prefix + entry["file"]
        entry["local_path"] = "releases/" + release + "/" + entry["file"]
    directory = Path(directory)
    for name, body in bodies.items():
        atomic(directory / "releases" / release / name, body)
    raw = encoded(manifest)
    if len(raw) > MAX_OBJECT:
        raise ValueError("Manifest exceeds serving budget")
    relative = "releases/" + release + "/manifest.json"
    atomic(directory / relative, raw)
    return dict(
        release_id=release,
        key=prefix + "manifest.json",
        sha256=digest(raw),
        relative_manifest=relative,
        series_count=len(entries),
        observations=manifest["observation_count"],
        missing=manifest["missing_count"],
    )
