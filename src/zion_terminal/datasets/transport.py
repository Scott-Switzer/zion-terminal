"""Bounded read-only transport for the private Zion dataset bridge.

The transport never uploads, mutates, or lists beyond an explicit prefix. Every
object read is ETag-pinned and byte-bounded, so a caller cannot accidentally pull
a multi-gigabyte archive into memory. Live discovery uses only the loopback
operator bridge described in ``docs/dataset-service.md``; tests and offline runs
use :class:`FixtureBridge`, which serves synthetic in-memory objects through the
same interface.

Storage access is not financial validation or redistribution permission.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8791"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MAX_READ_BYTES = 1024 * 1024
MAX_LIST_LIMIT = 200
DEFAULT_RETRIES = 3

STORE_IDS = ("financial", "research", "filings", "lakehouse")


class TransportError(RuntimeError):
    """A bounded dataset read failed. Never carries private object contents."""

    def __init__(
        self, code: str, message: str, *, status: int | None = None, retryable: bool = False
    ):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


class BridgeUrlError(TransportError):
    """The configured bridge origin is not a loopback operator endpoint."""

    def __init__(self, message: str):
        super().__init__("BRIDGE_NOT_LOOPBACK", message)


def validate_bridge_url(url: str) -> str:
    """Return a normalized loopback bridge origin or raise."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http":
        raise BridgeUrlError("the private bridge is plain HTTP on loopback only")
    if parsed.hostname not in LOOPBACK_HOSTS:
        raise BridgeUrlError("refusing a non-loopback dataset bridge host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BridgeUrlError("the dataset bridge origin must not carry credentials or query text")
    if parsed.path not in ("", "/"):
        raise BridgeUrlError("the dataset bridge origin must not carry a path")
    try:
        port = parsed.port
    except ValueError as exc:  # pragma: no cover - malformed port
        raise BridgeUrlError("invalid dataset bridge port") from exc
    return f"http://{parsed.hostname}{f':{port}' if port else ''}"


@dataclass(frozen=True)
class ObjectMeta:
    """Identity of one stored object as reported by the service."""

    store: str
    path: str
    bytes: int
    etag: str

    def as_dict(self) -> dict[str, Any]:
        return {"store": self.store, "path": self.path, "bytes": self.bytes, "etag": self.etag}


@dataclass(frozen=True)
class ListingPage:
    """One bounded directory page. ``cursor`` is opaque and scope-checked by the service."""

    store: str
    prefix: str
    objects: tuple[ObjectMeta, ...]
    prefixes: tuple[str, ...]
    truncated: bool
    cursor: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "store": self.store,
            "prefix": self.prefix,
            "objects": [item.as_dict() for item in self.objects],
            "prefixes": list(self.prefixes),
            "truncated": self.truncated,
            "cursor": self.cursor,
        }


class Bridge(Protocol):
    """Minimal transport contract shared by the live bridge and fixtures."""

    def list_objects(
        self,
        store: str,
        prefix: str = "",
        *,
        limit: int = 50,
        recursive: bool = False,
        cursor: str | None = None,
    ) -> ListingPage: ...

    def head(self, store: str, path: str) -> ObjectMeta: ...

    def read_range(
        self, store: str, path: str, offset: int, length: int, *, etag: str
    ) -> bytes: ...


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _retryable_status(status: int) -> bool:
    # 404 is retried deliberately: the remote operator binding intermittently
    # reports a missing object for a key that exists, and treating that as
    # "absent" would silently look like a data-quality finding.
    return status in {404, 429, 500, 502, 503, 504}


class HttpDatasetBridge:
    """Loopback-only client for the ``zion-datasets`` read API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BRIDGE_URL,
        *,
        timeout: float = 60.0,
        retries: int = DEFAULT_RETRIES,
        backoff: float = 1.0,
        sleeper: Any = time.sleep,
    ):
        self.base_url = validate_bridge_url(base_url)
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self._sleep = sleeper
        self.reads = 0
        self.bytes_read = 0
        self.retries_used = 0

    # -- internals ---------------------------------------------------------
    def _request(
        self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None
    ) -> bytes:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(url, method=method, headers=headers or {})
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read(MAX_READ_BYTES + 1)
            except urllib.error.HTTPError as exc:
                last = exc
                if not _retryable_status(exc.code):
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            if attempt < self.retries:
                self.retries_used += 1
                self._sleep(self.backoff * (attempt + 1))
        raise self._as_error(last)

    def _as_error(self, exc: Exception | None) -> TransportError:
        if isinstance(exc, urllib.error.HTTPError):
            code = {
                404: "OBJECT_NOT_FOUND",
                412: "ETAG_CHANGED",
                413: "READ_BUDGET_EXCEEDED",
                416: "RANGE_OUTSIDE_OBJECT",
                428: "PRECONDITION_REQUIRED",
            }.get(exc.code, "STORAGE_UNAVAILABLE")
            return TransportError(code, f"dataset service returned {exc.code}", status=exc.code)
        if isinstance(exc, urllib.error.URLError):
            return TransportError(
                "BRIDGE_UNREACHABLE",
                "the loopback dataset bridge is not reachable; start it with wrangler dev",
                retryable=True,
            )
        return TransportError("STORAGE_UNAVAILABLE", "dataset read failed")

    # -- Bridge ------------------------------------------------------------
    def list_objects(
        self,
        store: str,
        prefix: str = "",
        *,
        limit: int = 50,
        recursive: bool = False,
        cursor: str | None = None,
    ) -> ListingPage:
        if limit < 1 or limit > MAX_LIST_LIMIT:
            raise TransportError("INVALID_LISTING_LIMIT", f"limit must be 1..{MAX_LIST_LIMIT}")
        query = {"prefix": prefix, "limit": limit, "recursive": str(recursive).lower()}
        if cursor:
            query["cursor"] = cursor
        url = f"{self.base_url}/v1/datasets/{store}/objects?" + urllib.parse.urlencode(query)
        raw = self._request(url)
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise TransportError("LISTING_INVALID", "dataset listing was not JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("objects"), list):
            raise TransportError("LISTING_INVALID", "dataset listing has no object list")
        objects = tuple(
            ObjectMeta(
                store=store, path=item["path"], bytes=int(item["bytes"]), etag=str(item["etag"])
            )
            for item in payload["objects"]
        )
        return ListingPage(
            store=store,
            prefix=prefix,
            objects=objects,
            prefixes=tuple(payload.get("prefixes") or ()),
            truncated=bool(payload.get("truncated")),
            cursor=payload.get("cursor"),
        )

    def head(self, store: str, path: str) -> ObjectMeta:
        return self.head_meta(store, path)

    def read_range(self, store: str, path: str, offset: int, length: int, *, etag: str) -> bytes:
        if not 1 <= length <= MAX_READ_BYTES:
            raise TransportError(
                "READ_BUDGET_EXCEEDED", f"range length must be 1..{MAX_READ_BYTES}"
            )
        query = urllib.parse.urlencode({"path": path, "offset": offset, "length": length})
        url = f"{self.base_url}/v1/datasets/{store}/object?" + query
        raw = self._request(url, headers={"If-Match": etag})
        if len(raw) > length:
            raise TransportError(
                "READ_BUDGET_EXCEEDED", "dataset service returned more bytes than requested"
            )
        self.reads += 1
        self.bytes_read += len(raw)
        return raw

    # -- convenience -------------------------------------------------------
    def head_meta(self, store: str, path: str) -> ObjectMeta:
        """HEAD with the service-reported size and ETag (not a body read)."""
        url = f"{self.base_url}/v1/datasets/{store}/object?" + urllib.parse.urlencode(
            {"path": path}
        )
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return ObjectMeta(
                        store=store,
                        path=path,
                        bytes=int(response.headers.get("Content-Length", "0")),
                        etag=response.headers.get("ETag", ""),
                    )
            except urllib.error.HTTPError as exc:
                last = exc
                if not _retryable_status(exc.code):
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            if attempt < self.retries:
                self.retries_used += 1
                self._sleep(self.backoff * (attempt + 1))
        raise self._as_error(last)

    def read_all(self, store: str, path: str, *, max_bytes: int) -> tuple[bytes, ObjectMeta]:
        """Read a whole object in bounded ranges, refusing anything above ``max_bytes``."""
        meta = self.head_meta(store, path)
        if meta.bytes > max_bytes:
            raise TransportError(
                "READ_BUDGET_EXCEEDED",
                f"{path} is {meta.bytes} bytes; the bounded budget is {max_bytes}",
            )
        chunks: list[bytes] = []
        offset = 0
        while offset < meta.bytes:
            length = min(MAX_READ_BYTES, meta.bytes - offset)
            chunk = self.read_range(store, path, offset, length, etag=meta.etag)
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
        payload = b"".join(chunks)
        if len(payload) != meta.bytes:
            raise TransportError(
                "READ_INCOMPLETE", f"{path} returned {len(payload)} of {meta.bytes} bytes"
            )
        return payload, meta

    def iter_objects(
        self,
        store: str,
        prefix: str,
        *,
        limit: int = 50,
        recursive: bool = False,
        max_pages: int = 5,
        max_objects: int = 500,
    ) -> Iterator[ListingPage]:
        """Walk at most ``max_pages`` pages, following cursors while truncated.

        Pages are yielded even when a page carries no objects, because the service
        can return a truncated page whose only content is deeper prefixes.
        """
        cursor: str | None = None
        seen = 0
        for _ in range(max_pages):
            page = self.list_objects(store, prefix, limit=limit, recursive=recursive, cursor=cursor)
            yield page
            seen += len(page.objects)
            if not page.truncated or not page.cursor or seen >= max_objects:
                return
            cursor = page.cursor


@dataclass
class FixtureObject:
    """One synthetic object for :class:`FixtureBridge`."""

    payload: bytes
    etag: str = "etag-1"


@dataclass
class FixtureBridge:
    """Deterministic offline bridge used by tests and replayed reports."""

    objects: dict[tuple[str, str], FixtureObject] = field(default_factory=dict)
    listings: dict[tuple[str, str, bool], ListingPage] = field(default_factory=dict)
    failures: dict[tuple[str, str], str] = field(default_factory=dict)
    list_calls: list[dict[str, Any]] = field(default_factory=list)
    etag_versions: dict[tuple[str, str], str] = field(default_factory=dict)

    def put(self, store: str, path: str, payload: bytes, *, etag: str = "etag-1") -> None:
        self.objects[(store, path)] = FixtureObject(payload=payload, etag=etag)

    def put_json(self, store: str, path: str, value: Any, *, etag: str = "etag-1") -> None:
        self.put(store, path, json.dumps(value, sort_keys=True).encode(), etag=etag)

    def drop(self, store: str, path: str) -> None:
        self.objects.pop((store, path), None)

    def fail(self, store: str, path: str, code: str) -> None:
        """Force ``select``-time behaviour for ``path`` (``not_found``/``etag_changed``)."""
        self.failures[(store, path)] = code

    def list_objects(
        self,
        store: str,
        prefix: str = "",
        *,
        limit: int = 50,
        recursive: bool = False,
        cursor: str | None = None,
    ) -> ListingPage:
        self.list_calls.append(
            {
                "store": store,
                "prefix": prefix,
                "limit": limit,
                "recursive": recursive,
                "cursor": cursor,
            }
        )
        scripted = self.listings.get((store, prefix, recursive))
        if scripted is not None:
            if cursor == "next":
                return ListingPage(store, prefix, (), (), False, None)
            return scripted
        matching = [
            (path, item)
            for (obj_store, path), item in sorted(self.objects.items())
            if obj_store == store and path.startswith(prefix)
        ]
        if recursive:
            objects = tuple(
                ObjectMeta(store=store, path=path, bytes=len(item.payload), etag=item.etag)
                for path, item in matching
            )
            return ListingPage(store, prefix, objects, (), False, None)
        children: set[str] = set()
        objects_list = []
        for path, item in matching:
            tail = path[len(prefix) :]
            if "/" in tail:
                children.add(f"{prefix}{tail.split('/')[0]}/")
            else:
                objects_list.append(
                    ObjectMeta(store=store, path=path, bytes=len(item.payload), etag=item.etag)
                )
        return ListingPage(store, prefix, tuple(objects_list), tuple(sorted(children)), False, None)

    def script_listing(
        self, store: str, prefix: str, page: ListingPage, *, recursive: bool = False
    ) -> None:
        self.listings[(store, prefix, recursive)] = page

    def head(self, store: str, path: str) -> ObjectMeta:
        item = self._require(store, path)
        return ObjectMeta(store=store, path=path, bytes=len(item.payload), etag=item.etag)

    def read_range(self, store: str, path: str, offset: int, length: int, *, etag: str) -> bytes:
        if not 1 <= length <= MAX_READ_BYTES:
            raise TransportError("READ_BUDGET_EXCEEDED", "range length must be 1..1048576")
        item = self._require(store, path)
        if item.etag != etag:
            raise TransportError("ETAG_CHANGED", "object changed during read", status=412)
        return item.payload[offset : offset + length]

    def read_all(self, store: str, path: str, *, max_bytes: int) -> tuple[bytes, ObjectMeta]:
        item = self._require(store, path)
        if len(item.payload) > max_bytes:
            raise TransportError("READ_BUDGET_EXCEEDED", f"{path} exceeds the bounded budget")
        return item.payload, ObjectMeta(
            store=store, path=path, bytes=len(item.payload), etag=item.etag
        )

    def head_meta(self, store: str, path: str) -> ObjectMeta:
        return self.head(store, path)

    def _require(self, store: str, path: str) -> FixtureObject:
        if self.failures.get((store, path)) == "not_found":
            raise TransportError("OBJECT_NOT_FOUND", f"{path} is not present", status=404)
        if self.failures.get((store, path)) == "etag_changed":
            raise TransportError("ETAG_CHANGED", "object changed during read", status=412)
        item = self.objects.get((store, path))
        if item is None:
            raise TransportError("OBJECT_NOT_FOUND", f"{path} is not present", status=404)
        return item

    def iter_objects(
        self,
        store: str,
        prefix: str,
        *,
        limit: int = 50,
        recursive: bool = False,
        max_pages: int = 5,
        max_objects: int = 500,
    ) -> Iterator[ListingPage]:
        page = self.list_objects(store, prefix, limit=limit, recursive=recursive)
        yield page
        cursor = page.cursor
        pages = 1
        total = len(page.objects)
        while page.truncated and cursor and pages < max_pages:
            page = self.list_objects(store, prefix, limit=limit, recursive=recursive, cursor=cursor)
            yield page
            total += len(page.objects)
            pages += 1
            cursor = page.cursor
            if total > max_objects:
                return


class RangedObjectReader(io.RawIOBase):
    """Seekable read-only stream over one stored object, backed by bounded ranges.

    This is what makes selective reads of a large archive possible: a ZIP central
    directory and a single member can be read without transferring the whole
    object. The object ETag is pinned once, so a mixed-version read is a hard error.
    """

    def __init__(self, bridge: Bridge, store: str, path: str, *, size: int, etag: str):
        self._bridge = bridge
        self._store = store
        self._path = path
        self._pos = 0
        self.size = size
        self.etag = etag
        self.ranged_reads = 0
        self.ranged_bytes = 0

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return True

    def readable(self) -> bool:  # pragma: no cover - trivial
        return True

    def tell(self) -> int:  # pragma: no cover - trivial
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self.size + offset
        else:  # pragma: no cover - defensive
            raise ValueError("invalid whence")
        self._pos = max(0, min(self._pos, self.size))
        return self._pos

    def read(self, length: int = -1) -> bytes:
        if length is None or length < 0:
            length = self.size - self._pos
        length = min(length, self.size - self._pos)
        out = bytearray()
        while len(out) < length:
            take = min(MAX_READ_BYTES, length - len(out))
            chunk = self._bridge.read_range(
                self._store, self._path, self._pos, take, etag=self.etag
            )
            if not chunk:
                break
            out.extend(chunk)
            self._pos += len(chunk)
            self.ranged_reads += 1
            self.ranged_bytes += len(chunk)
        return bytes(out)

    def readinto(self, buffer: Any) -> int:  # pragma: no cover - exercised via zipfile
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def open_zip_member(
    bridge: Bridge,
    store: str,
    path: str,
    member: str,
    *,
    max_member_bytes: int,
) -> tuple[bytes, dict[str, Any]]:
    """Read one ZIP member through bounded ranged reads and verify its CRC32.

    Without this, a company's SEC facts inside a 1.4 GB nightly archive would be
    unreachable without downloading the entire object.
    """
    meta = bridge.head_meta(store, path)
    reader = RangedObjectReader(bridge, store, path, size=meta.bytes, etag=meta.etag)
    with zipfile.ZipFile(io.BufferedReader(reader, buffer_size=1 << 16)) as archive:
        try:
            info = archive.getinfo(member)
        except KeyError as exc:
            raise TransportError("ZIP_MEMBER_MISSING", f"{member} is not in {path}") from exc
        if info.file_size > max_member_bytes:
            raise TransportError(
                "READ_BUDGET_EXCEEDED",
                f"{member} is {info.file_size} bytes; the bounded budget is {max_member_bytes}",
            )
        payload = archive.read(member)
    evidence = {
        "store": store,
        "path": path,
        "object_bytes": meta.bytes,
        "object_etag": meta.etag,
        "member": member,
        "member_bytes": len(payload),
        "member_crc32": f"{info.CRC:08x}",
        "member_compressed_bytes": info.compress_size,
        "member_sha256": sha256_hex(payload),
        "ranged_reads": reader.ranged_reads,
        "ranged_bytes": reader.ranged_bytes,
    }
    return payload, evidence
