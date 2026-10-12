"""Private financial dataset registry for the Zion R2 stores.

The registry turns bounded storage discovery into reviewable dataset records:
which source family was listed, which release pointer and manifest were resolved,
which object bytes were hash-verified, and what a stored object actually claims.

Discovery is not validation. A successful listing, a readable object or a large
aggregate row count never promotes a financial observation to ``PASS``; only the
fundamentals contract in :mod:`zion_terminal.datasets.sec_fundamentals` can do
that, and only from hash-verified source bytes. Every record carries the store,
path, ETag, size and SHA-256 it was read from, and any listing that hit its page
or read bound is labelled as partial.

Nothing here writes, copies, publishes or re-points a dataset.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from zion_terminal.datasets.sec_fundamentals import SourceRecord
from zion_terminal.datasets.transport import (
    MAX_READ_BYTES,
    Bridge,
    ObjectMeta,
    TransportError,
    open_zip_member,
    sha256_hex,
)

QUALITY_DISCOVERED = "DISCOVERED"
QUALITY_HASH_VERIFIED = "HASH_VERIFIED"
QUALITY_SCHEMA_VALIDATED = "SCHEMA_VALIDATED"
QUALITY_SOURCE_UNVALIDATED = "SOURCE_UNVALIDATED"
QUALITY_ABSENT = "ABSENT"
QUALITY_BLOCKED = "BLOCKED"
QUALITY_PARTIAL = "PARTIAL"

DEFAULT_OBJECT_BUDGET = 6 * 1024 * 1024
DEFAULT_MEMBER_BUDGET = 32 * 1024 * 1024
CIK_TIMESTAMP_RE = re.compile(r"^cik=(\d{10})$")


@dataclass(frozen=True)
class SourceFamily:
    """A named dataset family inside one store."""

    family_id: str
    store: str
    label: str
    usage: str
    listing_prefix: str = ""
    pointer_key: str | None = None
    pointer_schema: str | None = None
    manifest_prefix: str | None = None
    publication: str = "PRIVATE_SERVICE_ONLY"

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "store": self.store,
            "label": self.label,
            "usage": self.usage,
            "listing_prefix": self.listing_prefix,
            "pointer_key": self.pointer_key,
            "pointer_schema": self.pointer_schema,
            "manifest_prefix": self.manifest_prefix,
            "publication": self.publication,
        }


SOURCE_FAMILIES: tuple[SourceFamily, ...] = (
    SourceFamily(
        family_id="serving_release_candidate",
        store="financial",
        label="Serving V2 candidate release",
        usage="Published serving representation consumed by the staging query worker",
        listing_prefix="gold/serving/coverage25/",
        pointer_key="gold/serving/coverage25/CURRENT.json",
        pointer_schema="financial-serving-v2",
        manifest_prefix="gold/serving/releases/",
    ),
    SourceFamily(
        family_id="serving_release_promoted",
        store="financial",
        label="Serving V2 promoted release",
        usage="Promoted serving representation pointer",
        listing_prefix="gold/serving/",
        pointer_key="gold/serving/CURRENT.json",
        pointer_schema="financial-serving-v2",
        manifest_prefix="gold/serving/releases/",
    ),
    SourceFamily(
        family_id="sec_facts_release",
        store="financial",
        label="Published SEC facts release",
        usage="Normalized point-in-time SEC facts with accession-level provenance",
        listing_prefix="manifests/sec-facts/",
        manifest_prefix="gold/sec-facts/releases/",
    ),
    SourceFamily(
        family_id="sec_companyfacts_corpus",
        store="filings",
        label="SEC filing-intelligence companyfacts corpus",
        usage="Raw SEC CompanyFacts per CIK inside the filing corpus",
        listing_prefix="sec-filing-intelligence/corpus/",
    ),
    SourceFamily(
        family_id="sec_bulk_archive",
        store="research",
        label="SEC nightly bulk archive",
        usage="SEC company facts/submissions archives in the private research store",
        listing_prefix="sec/bulk/",
    ),
    SourceFamily(
        family_id="company_dataset",
        store="financial",
        label="Per-company versioned dataset",
        usage="Versioned bronze dataset per ticker",
        listing_prefix="datasets/sec/",
    ),
    SourceFamily(
        family_id="native_sec_layer",
        store="financial",
        label="Native SEC research layer",
        usage="Sharded native SEC companyfacts and submissions layer",
        listing_prefix="research/sec-native-v1/",
    ),
    SourceFamily(
        family_id="valuation_packets",
        store="financial",
        label="Approved SEC valuation packets",
        usage="Approved public-SEC packet path used for ticker autofill",
        listing_prefix="gold/valuation/",
        pointer_key="control/valuation/CURRENT.json",
        pointer_schema="ppe-valuation-index-v1",
        manifest_prefix="gold/valuation/releases/",
    ),
    SourceFamily(
        family_id="wrds_inventory",
        store="research",
        label="WRDS private research inventory",
        usage="Private WRDS library/table inventory; no redistribution rights implied",
        listing_prefix="inventory/releases/",
    ),
)

FAMILY_INDEX = {family.family_id: family for family in SOURCE_FAMILIES}


@dataclass
class ObjectRecord:
    """What one stored object or archive member is, and how it was read."""

    store: str
    path: str
    bytes: int | None
    etag: str | None = None
    sha256: str | None = None
    quality: str = QUALITY_HASH_VERIFIED
    release_id: str | None = None
    schema_version: str | None = None
    member: str | None = None
    member_crc32: str | None = None
    member_compressed_bytes: int | None = None
    ranged_reads: int | None = None
    ranged_bytes: int | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "store": self.store,
                "path": self.path,
                "bytes": self.bytes,
                "etag": self.etag,
                "sha256": self.sha256,
                "quality": self.quality,
                "release_id": self.release_id,
                "schema_version": self.schema_version,
                "member": self.member,
                "member_crc32": self.member_crc32,
                "member_compressed_bytes": self.member_compressed_bytes,
                "ranged_reads": self.ranged_reads,
                "ranged_bytes": self.ranged_bytes,
            }.items()
            if value is not None
        } | ({"notes": list(self.notes)} if self.notes else {})

    def source_record(self) -> SourceRecord:
        return SourceRecord(
            store=self.store,
            path=self.path,
            sha256=self.sha256 or "",
            etag=self.etag,
            bytes=self.bytes,
            member=self.member,
            member_crc32=self.member_crc32,
            release_id=self.release_id,
            schema_version=self.schema_version,
        )


@dataclass
class Discovery:
    """A bounded listing result that never claims completeness."""

    family_id: str
    store: str
    prefix: str
    recursive: bool
    objects: list[ObjectMeta] = field(default_factory=list)
    prefixes: list[str] = field(default_factory=list)
    pages: int = 0
    complete: bool = False
    quality: str = QUALITY_DISCOVERED
    findings: list[str] = field(default_factory=list)

    def paths(self) -> list[str]:
        return [item.path for item in self.objects]

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "store": self.store,
            "prefix": self.prefix,
            "recursive": self.recursive,
            "pages": self.pages,
            "complete": self.complete,
            "quality": self.quality,
            "object_count": len(self.objects),
            "objects": [item.as_dict() for item in self.objects],
            "child_prefixes": list(self.prefixes),
            "findings": list(self.findings),
        }


@dataclass
class ReleaseRef:
    """A resolved release pointer plus its manifest."""

    family_id: str
    pointer_record: ObjectRecord
    release_id: str
    manifest_key: str
    manifest_record: ObjectRecord
    manifest: dict[str, Any]
    pointer: dict[str, Any]
    schema_version: str | None = None
    manifest_prefix: str = "gold/serving/releases/"
    artifact_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    artifact_sample: dict[str, Any] = field(default_factory=dict)
    complete: bool = False

    def artifact(self, relative: str) -> dict[str, Any] | None:
        return self.artifact_index.get(relative)

    def artifacts(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("artifacts") or ())

    def released_object_path(self, relative: str) -> str:
        return f"{self.manifest_prefix}{relative}"

    def as_dict(self, *, include_artifacts: bool = False) -> dict[str, Any]:
        body = {
            "family_id": self.family_id,
            "pointer": self.pointer_record.as_dict(),
            "release_id": self.release_id,
            "manifest_key": self.manifest_key,
            "manifest": self.manifest_record.as_dict(),
            "schema_version": self.schema_version,
            "artifact_count": len(self.artifact_index),
            "artifact_sample": self.artifact_sample,
            "complete": self.complete,
            "findings": list(self.findings),
            "manifest_summary": {
                key: value
                for key, value in self.manifest.items()
                if key != "artifacts" and isinstance(value, (str, int, float, bool, type(None)))
            },
        }
        if include_artifacts:
            body["artifacts"] = self.artifacts()
        return body


class FinancialDatasetRegistry:
    """Read-only registry over the four private financial R2 stores."""

    def __init__(
        self,
        bridge: Bridge,
        *,
        object_budget: int = DEFAULT_OBJECT_BUDGET,
        member_budget: int = DEFAULT_MEMBER_BUDGET,
        store_probe: bool = False,
    ):
        self.bridge = bridge
        self.object_budget = object_budget
        self.member_budget = member_budget
        self.store_probe = store_probe
        self.inventory: list[ObjectRecord] = []

    # -- discovery ---------------------------------------------------------
    def catalog(self) -> dict[str, Any]:
        probed = self._read_json(
            "financial", "gold/serving/coverage25/CURRENT.json", budget=64 * 1024, optional=True
        )
        pointer_value, pointer_record = (None, None) if probed is None else probed
        return {
            "schema_version": "zion-dataset-registry-catalog-v1",
            "stores": sorted({family.store for family in SOURCE_FAMILIES}),
            "families": [family.as_dict() for family in SOURCE_FAMILIES],
            "pointer_probe": pointer_record.as_dict() if pointer_record else None,
            "pointer_value": pointer_value,
            "limits": {"objects_per_page": 200, "bytes_per_read": MAX_READ_BYTES},
            "warning": (
                "Configured families are a declaration, not evidence. Connectivity, stored "
                "timestamps and ETags do not establish availability, freshness, financial "
                "accuracy or redistribution rights."
            ),
        }

    def discover(
        self,
        family_id: str,
        *,
        prefix: str | None = None,
        recursive: bool = False,
        limit: int = 25,
        max_pages: int = 2,
        max_objects: int = 50,
    ) -> Discovery:
        family = self._family(family_id)
        target = prefix if prefix is not None else family.listing_prefix
        discovery = Discovery(
            family_id=family.family_id,
            store=family.store,
            prefix=target,
            recursive=recursive,
        )
        try:
            for page in self.bridge.iter_objects(
                family.store,
                target,
                limit=limit,
                recursive=recursive,
                max_pages=max_pages,
                max_objects=max_objects,
            ):
                discovery.pages += 1
                discovery.objects.extend(page.objects)
                discovery.prefixes.extend(page.prefixes)
                if not page.truncated:
                    discovery.complete = True
                    break
        except TransportError as error:
            discovery.quality = QUALITY_BLOCKED
            discovery.findings.append(f"{error.code}: {error.message}")
            return discovery
        discovery.prefixes = sorted(set(discovery.prefixes))
        if not discovery.complete:
            discovery.quality = QUALITY_PARTIAL
            discovery.findings.append("LISTING_BOUNDED_BEFORE_COMPLETION")
        return discovery

    def find_in_prefix(
        self,
        family_id: str,
        prefix: str,
        suffix: str,
        *,
        recursive: bool = True,
        max_pages: int = 3,
    ) -> Discovery:
        discovery = self.discover(
            family_id,
            prefix=prefix,
            recursive=recursive,
            max_pages=max_pages,
            limit=50,
            max_objects=200,
        )
        discovery.objects = [item for item in discovery.objects if item.path.endswith(suffix)]
        return discovery

    # -- release resolution ------------------------------------------------
    def resolve_release(
        self,
        family_id: str,
        *,
        pointer_key: str | None = None,
        manifest_prefix: str | None = None,
    ) -> ReleaseRef:
        family = self._family(family_id)
        key = pointer_key or family.pointer_key
        if not key:
            raise TransportError("FAMILY_HAS_NO_POINTER", f"{family_id} has no release pointer")
        prefix = manifest_prefix or family.manifest_prefix or ""
        pointer, pointer_record = self._read_json(family.store, key, budget=64 * 1024)
        if not isinstance(pointer, dict):
            raise TransportError("POINTER_INVALID", f"{key} is not an object")
        expected_schema = family.pointer_schema
        if expected_schema and pointer.get("schema_version") != expected_schema:
            raise TransportError(
                "POINTER_SCHEMA_MISMATCH",
                f"{key} declares {pointer.get('schema_version')!r}, expected {expected_schema!r}",
            )
        release_id = pointer.get("serving_release_id")
        manifest_key = pointer.get("manifest_key")
        if not isinstance(release_id, str) or not release_id:
            raise TransportError("POINTER_INVALID", f"{key} has no release identity")
        if not isinstance(manifest_key, str) or not manifest_key.startswith(prefix):
            if not isinstance(manifest_key, str):
                raise TransportError("POINTER_INVALID", f"{key} has no safe manifest key")
            raise TransportError(
                "MANIFEST_KEY_OUTSIDE_FAMILY",
                f"{manifest_key} is outside the expected release namespace",
            )
        manifest, manifest_record = self._read_json(
            family.store, manifest_key, budget=self.object_budget
        )
        if not isinstance(manifest, dict):
            raise TransportError("MANIFEST_INVALID", f"{manifest_key} is not an object")
        release = ReleaseRef(
            family_id=family.family_id,
            pointer_record=pointer_record,
            release_id=release_id,
            manifest_key=manifest_key,
            manifest_record=manifest_record,
            manifest=manifest,
            pointer=pointer,
            schema_version=manifest.get("schema_version"),
            manifest_prefix=manifest_key[: manifest_key.rindex("/") + 1],
        )
        release.manifest_record.release_id = release_id
        release.manifest_record.schema_version = release.schema_version
        expected_hash = pointer.get("manifest_sha256")
        if expected_hash:
            if manifest_record.sha256 != expected_hash:
                raise TransportError(
                    "MANIFEST_HASH_MISMATCH", "manifest hash does not match the pointer"
                )
            release.findings.append("MANIFEST_SHA256_MATCH")
        else:
            release.findings.append("MANIFEST_HASH_NOT_PINNED")
        if manifest.get("serving_release_id") not in (None, release_id):
            raise TransportError(
                "MANIFEST_IDENTITY_MISMATCH", "manifest release identity disagrees with the pointer"
            )
        if expected_schema and release.schema_version != expected_schema:
            raise TransportError(
                "MANIFEST_SCHEMA_MISMATCH",
                f"manifest declares {release.schema_version!r}, expected {expected_schema!r}",
            )
        release.findings.append("MANIFEST_IDENTITY_OK")
        release.artifact_index = {
            item["path"]: item
            for item in (manifest.get("artifacts") or ())
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        return release

    def sample_artifacts(self, release: ReleaseRef, *, limit: int = 3) -> dict[str, Any]:
        """HEAD a bounded sample of manifest artifacts to detect a half-published release.

        This is deliberately a sample: it proves presence or absence for the sampled
        paths only and never asserts that the whole release is stored.
        """
        sample = self._sample_paths(release, limit=limit)
        present, missing, errors = [], [], []
        for path in sample:
            full = release.released_object_path(path)
            try:
                meta = self.bridge.head_meta(release.manifest_record.store, full)
                present.append({"path": full, "bytes": meta.bytes})
                self._remember(
                    ObjectRecord(
                        store=release.manifest_record.store,
                        path=full,
                        bytes=meta.bytes,
                        etag=meta.etag,
                        quality=QUALITY_DISCOVERED,
                        release_id=release.release_id,
                        notes=["manifest artifact present"],
                    )
                )
            except TransportError as error:
                if error.code == "OBJECT_NOT_FOUND":
                    missing.append(full)
                else:
                    errors.append({"path": full, "error": error.code})
        evidence = {
            "sampled": len(sample),
            "sampled_paths": [release.released_object_path(path) for path in sample],
            "present": present,
            "missing": missing,
            "errors": errors,
            "complete": len(missing) == 0 and len(errors) == 0,
        }
        release.artifact_sample = evidence
        release.complete = evidence["complete"]
        if missing:
            release.findings.append("MANIFEST_ARTIFACTS_NOT_STORED")
        if errors:
            release.findings.append("ARTIFACT_PROBE_INCOMPLETE")
        return evidence

    @staticmethod
    def _sample_paths(release: ReleaseRef, *, limit: int) -> list[str]:
        candidates = [
            path
            for path in release.artifact_index
            if path.startswith(("identity/", "entities/", "catalog/", "corporate-actions/"))
        ]
        # Prefer one identity artifact, one entity artifact and the metric catalog,
        # so a sample is representative of the three artifact classes.
        ordered: list[str] = []
        for wanted in ("identity/", "entities/", "catalog/"):
            match = sorted(path for path in candidates if path.startswith(wanted))
            if match:
                ordered.append(match[0])
        for path in sorted(candidates):
            if path not in ordered:
                ordered.append(path)
        return ordered[:limit]

    # -- object reads ------------------------------------------------------
    def read_object(
        self, store: str, path: str, *, budget: int | None = None, release_id: str | None = None
    ) -> tuple[bytes, ObjectRecord]:
        limit = self.object_budget if budget is None else budget
        payload, meta = self.bridge.read_all(store, path, max_bytes=limit)
        record = ObjectRecord(
            store=store,
            path=path,
            bytes=len(payload),
            etag=meta.etag,
            sha256=sha256_hex(payload),
            quality=QUALITY_HASH_VERIFIED,
            release_id=release_id,
        )
        self._remember(record)
        return payload, record

    def read_zip_member(
        self,
        store: str,
        path: str,
        member: str,
        *,
        budget: int | None = None,
        release_id: str | None = None,
    ) -> tuple[bytes, ObjectRecord]:
        limit = self.member_budget if budget is None else budget
        payload, evidence = open_zip_member(
            self.bridge, store, path, member, max_member_bytes=limit
        )
        if not evidence["member_crc32"]:
            raise TransportError("ZIP_MEMBER_CRC_MISSING", "archive member has no CRC32")
        record = ObjectRecord(
            store=store,
            path=path,
            bytes=evidence["object_bytes"],
            etag=evidence["object_etag"],
            sha256=evidence["member_sha256"],
            quality=QUALITY_HASH_VERIFIED,
            release_id=release_id,
            member=member,
            member_crc32=evidence["member_crc32"],
            member_compressed_bytes=evidence["member_compressed_bytes"],
            ranged_reads=evidence["ranged_reads"],
            ranged_bytes=evidence["ranged_bytes"],
            notes=["ETag-pinned ranged reads; member CRC32 from the archive directory"],
        )
        self._remember(record)
        return payload, record

    def _read_json(
        self,
        store: str,
        path: str,
        *,
        budget: int,
        optional: bool = False,
    ) -> tuple[Any, ObjectRecord] | None:
        try:
            payload, record = self.read_object(store, path, budget=budget)
        except TransportError as error:
            if optional and error.code == "OBJECT_NOT_FOUND":
                metadata = ObjectRecord(
                    store=store, path=path, bytes=None, quality=QUALITY_ABSENT, notes=[error.code]
                )
                self._remember(metadata)
                return None
            raise
        try:
            value = json.loads(payload)
        except ValueError as error:
            raise TransportError("OBJECT_NOT_JSON", f"{path} is not JSON") from error
        return value, record

    # -- SEC source resolution --------------------------------------------
    def resolve_sec_facts_release(self) -> ReleaseRef | None:
        """Find the newest published SEC facts manifest by bounded listing."""
        discovery = self.find_in_prefix(
            "sec_facts_release", "manifests/sec-facts/", ".json", recursive=True, max_pages=1
        )
        if not discovery.objects:
            return None
        newest = max(discovery.objects, key=lambda item: item.path)
        manifest, record = self._read_json("financial", newest.path, budget=256 * 1024)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("prefix"), str):
            raise TransportError("MANIFEST_INVALID", f"{newest.path} has no release prefix")
        release = ReleaseRef(
            family_id="sec_facts_release",
            pointer_record=record,
            release_id=newest.path.rsplit("/", 1)[-1].removesuffix(".json"),
            manifest_key=newest.path,
            manifest_record=record,
            manifest=manifest,
            pointer=manifest,
            schema_version=manifest.get("schema_version"),
            manifest_prefix=manifest["prefix"] + "/",
        )
        release.findings.append("MANIFEST_LISTED_NOT_POINTERED")
        if manifest.get("content_sha256") and manifest["content_sha256"] != release.release_id:
            release.findings.append("MANIFEST_CONTENT_HASH_DIFFERS_FROM_FILENAME")
        release.artifact_index = {
            item["path"]: item
            for item in (manifest.get("files") or ())
            if isinstance(item, dict) and "path" in item
        }
        return release

    def published_security(
        self, ticker: str
    ) -> tuple[dict[str, Any] | None, ObjectRecord | None, ReleaseRef | None]:
        release = self.resolve_sec_facts_release()
        if release is None:
            return None, None, None
        relative = f"securities/{ticker.upper()}.json"
        entry = release.artifact(relative)
        path = release.manifest_prefix + relative
        if entry is None:
            missing = ObjectRecord(
                store="financial",
                path=path,
                bytes=None,
                quality=QUALITY_ABSENT,
                notes=["not_in_manifest"],
            )
            return None, missing, release
        payload, record = self._read_json("financial", path, budget=256 * 1024, optional=True)
        if record.quality == QUALITY_ABSENT:
            return None, record, release
        record.release_id = release.release_id
        record.schema_version = release.schema_version
        if entry.get("sha256"):
            if record.sha256 == entry["sha256"]:
                record.quality = QUALITY_SCHEMA_VALIDATED
                record.notes.append("sha256 matches release manifest pin")
            else:
                record.notes.append("sha256 differs from release manifest pin")
                record.quality = QUALITY_SOURCE_UNVALIDATED
        return payload, record, release

    def valuation_index(self) -> tuple[dict[str, Any], ObjectRecord]:
        payload, record = self._read_json(
            "financial", "control/valuation/CURRENT.json", budget=1024 * 1024
        )
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "ppe-valuation-index-v1"
        ):
            raise TransportError(
                "VALUATION_INDEX_INVALID", "approved packet index has an unexpected schema"
            )
        record.release_id = "control/valuation"
        record.schema_version = payload["schema_version"]
        record.quality = QUALITY_SCHEMA_VALIDATED
        return payload, record

    def valuation_packet(self, ticker: str) -> tuple[dict[str, Any] | None, ObjectRecord | None]:
        index, _ = self.valuation_index()
        entry = (index.get("companies") or {}).get(ticker.upper())
        if not isinstance(entry, dict):
            return None, None
        key = entry.get("key")
        digest = entry.get("sha256")
        if not isinstance(key, str) or not re.fullmatch(
            r"gold/valuation/releases/[a-f0-9]{64}/companies/"
            + re.escape(ticker.upper())
            + r"\.json",
            key,
        ):
            raise TransportError(
                "VALUATION_PACKET_REFERENCE_INVALID", "approved packet reference is not well formed"
            )
        payload, record = self._read_json("financial", key, budget=256 * 1024, optional=True)
        if record.quality == QUALITY_ABSENT:
            return None, record
        record.release_id = key.split("/")[3]
        record.schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
        if digest and record.sha256 == digest:
            record.quality = QUALITY_SCHEMA_VALIDATED
            record.notes.append("sha256 matches approved index pin")
        else:
            record.notes.append("sha256 differs from approved index pin")
            record.quality = QUALITY_SOURCE_UNVALIDATED
        if isinstance(payload, dict) and payload.get("schema_version") != "ppe-valuation-packet-v1":
            raise TransportError(
                "VALUATION_PACKET_SCHEMA_MISMATCH", "approved packet schema is unexpected"
            )
        return payload, record

    def company_identity(self, ticker: str) -> tuple[str | None, ObjectRecord | None]:
        """Resolve an issuer CIK from the per-company versioned dataset.

        This is the third identity source after an approved packet and the published
        SEC facts release, and the only one that covers a company that has never been
        published to either.
        """
        ticker = ticker.upper()
        versions = self.discover(
            "company_dataset",
            prefix=f"datasets/sec/{ticker}/versions/",
            max_pages=1,
            limit=25,
            max_objects=25,
        )
        if not versions.prefixes:
            return None, None
        latest = sorted(versions.prefixes)[-1]
        files = self.discover(
            "company_dataset", prefix=latest, max_pages=1, limit=25, max_objects=25
        )
        data = next((item for item in files.objects if item.path.endswith("/data.json")), None)
        if data is None:
            return None, None
        payload, record = self._read_json(
            "financial", data.path, budget=2 * 1024 * 1024, optional=True
        )
        if not isinstance(payload, dict) or payload.get("cik") is None:
            return None, record
        record.schema_version = (
            str(payload.get("schema_version")) if payload.get("schema_version") else None
        )
        record.release_id = latest.rstrip("/").split("/")[-1]
        return str(payload["cik"]).zfill(10), record

    def corporate_facts_sources(
        self, cik: str, *, sec_facts_release: ReleaseRef | None = None
    ) -> list[dict[str, Any]]:
        """Ordered, bounded candidate sources for one issuer's raw SEC facts."""
        padded = cik.zfill(10)
        candidates: list[dict[str, Any]] = []
        corpus_prefix = None
        if sec_facts_release is not None:
            corpus = sec_facts_release.manifest.get("source_corpus")
            if isinstance(corpus, str) and corpus:
                corpus_prefix = corpus.rstrip("/")
        if corpus_prefix:
            candidates.append(
                {
                    "store": "filings",
                    "path": f"{corpus_prefix}/raw/sec/companyfacts/cik={padded}/companyfacts.json",
                    "kind": "corpus_companyfacts",
                    "origin": "sec filing-intelligence corpus",
                }
            )
        archive = self._sec_bulk_archive()
        if archive is not None:
            candidates.append(
                {
                    "store": "research",
                    "path": archive["zip"],
                    "member": f"CIK{padded}.json",
                    "kind": "bulk_archive_member",
                    "origin": archive["origin"],
                }
            )
        return candidates

    def _sec_bulk_archive(self) -> dict[str, Any] | None:
        discovery = self.discover(
            "sec_bulk_archive",
            prefix="sec/bulk/",
            recursive=False,
            max_pages=1,
            limit=25,
            max_objects=25,
        )
        sources = sorted(discovery.prefixes)
        if not sources:
            return None
        source = sources[-1]
        listing = self.discover(
            "sec_bulk_archive",
            prefix=source,
            recursive=False,
            max_pages=1,
            limit=25,
            max_objects=25,
        )
        for item in listing.objects:
            if item.path.endswith("companyfacts.zip"):
                return {
                    "zip": item.path,
                    "origin": f"SEC nightly bulk archive {source.rstrip('/').split('/')[-1]}",
                }
        return None

    # -- internals ---------------------------------------------------------
    def _family(self, family_id: str) -> SourceFamily:
        try:
            return FAMILY_INDEX[family_id]
        except KeyError as error:  # pragma: no cover - programmer error
            raise KeyError(f"unknown dataset family {family_id!r}") from error

    def _remember(self, record: ObjectRecord) -> None:
        self.inventory.append(record)

    def inventory_report(self) -> dict[str, Any]:
        return {
            "schema_version": "zion-dataset-registry-inventory-v1",
            "objects": [record.as_dict() for record in self.inventory],
        }
