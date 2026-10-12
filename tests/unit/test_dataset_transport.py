"""Transport-level tests: loopback guard, bounds, cursors, ETags and ZIP reads."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import zipfile

import pytest

from zion_terminal.datasets.transport import (
    MAX_READ_BYTES,
    BridgeUrlError,
    FixtureBridge,
    HttpDatasetBridge,
    ListingPage,
    ObjectMeta,
    RangedObjectReader,
    TransportError,
    open_zip_member,
    validate_bridge_url,
)


class FakeHeaders(dict):
    def get(self, key, default=None):  # noqa: A003 - mimics http.client.HTTPMessage
        return dict.get(self, key, default)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None):
        super().__init__(payload)
        self.headers = FakeHeaders(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_bridge_url_must_be_plain_loopback_http():
    assert validate_bridge_url("http://127.0.0.1:8791") == "http://127.0.0.1:8791"
    assert validate_bridge_url("http://localhost:8791/") == "http://localhost:8791"
    for bad in (
        "https://127.0.0.1:8791",
        "http://example.com:8791",
        "http://user:pass@127.0.0.1:8791",
        "http://127.0.0.1:8791/v1/datasets",
        "http://127.0.0.1:8791?path=secret",
    ):
        with pytest.raises(BridgeUrlError):
            validate_bridge_url(bad)


def test_http_bridge_validates_host_before_any_request():
    with pytest.raises(BridgeUrlError):
        HttpDatasetBridge("http://storage.example.com")


def test_http_bridge_retries_a_transient_missing_object_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def urlopen(request, timeout=None):  # noqa: ARG001 - matches urllib signature
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return FakeResponse(b"payload", {"ETag": '"v1"', "Content-Length": "7"})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    bridge = HttpDatasetBridge("http://127.0.0.1:8791", backoff=0, sleeper=lambda _: None)
    assert bridge.read_range("financial", "a/b.json", 0, 7, etag='"v1"') == b"payload"
    assert calls["n"] == 3
    assert bridge.retries_used == 2


def test_http_bridge_fails_closed_when_the_object_stays_absent(monkeypatch):
    def urlopen(request, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    bridge = HttpDatasetBridge(
        "http://127.0.0.1:8791", backoff=0, retries=2, sleeper=lambda _: None
    )
    with pytest.raises(TransportError) as error:
        bridge.read_range("financial", "missing.json", 0, 4, etag='"v1"')
    assert error.value.code == "OBJECT_NOT_FOUND"
    assert bridge.retries_used == 2


def test_http_bridge_maps_conditional_read_failures(monkeypatch):
    def urlopen(request, timeout=None):  # noqa: ARG001
        raise urllib.error.HTTPError(request.full_url, 412, "Precondition Failed", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    bridge = HttpDatasetBridge("http://127.0.0.1:8791", backoff=0, sleeper=lambda _: None)
    with pytest.raises(TransportError) as error:
        bridge.read_range("financial", "a/b.json", 0, 4, etag='"old"')
    assert error.value.code == "ETAG_CHANGED"


def test_read_range_and_read_all_enforce_the_byte_budget():
    bridge = FixtureBridge()
    bridge.put("financial", "big.bin", b"x" * (MAX_READ_BYTES + 10))
    with pytest.raises(TransportError):
        bridge.read_range("financial", "big.bin", 0, MAX_READ_BYTES + 1, etag="etag-1")
    with pytest.raises(TransportError) as error:
        bridge.read_all("financial", "big.bin", max_bytes=MAX_READ_BYTES)
    assert error.value.code == "READ_BUDGET_EXCEEDED"


def test_cursor_paging_follows_truncated_pages_even_when_a_page_is_empty():
    bridge = FixtureBridge()
    bridge.script_listing(
        "financial",
        "gold/serving/",
        ListingPage("financial", "gold/serving/", (), ("gold/serving/deeper/",), True, "next"),
    )
    pages = list(bridge.iter_objects("financial", "gold/serving/", max_pages=3))
    assert len(pages) == 2
    assert pages[0].truncated and pages[0].objects == ()
    assert pages[1].truncated is False
    assert bridge.list_calls[1]["cursor"] == "next"


def test_fixture_bridge_lists_directories_and_honours_recursion():
    bridge = FixtureBridge()
    bridge.put_json("financial", "gold/serving/CURRENT.json", {"a": 1})
    bridge.put_json("financial", "gold/serving/releases/r1/manifest.json", {"b": 2})
    page = bridge.list_objects("financial", "gold/serving/")
    assert [item.path for item in page.objects] == ["gold/serving/CURRENT.json"]
    assert page.prefixes == ("gold/serving/releases/",)
    recursive = bridge.list_objects("financial", "gold/serving/", recursive=True)
    assert len(recursive.objects) == 2
    assert page.truncated is False


def _archive(*, padding_members: int = 40, padding_bytes: int = 0) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("CIK0000000002.json", json.dumps({"cik": 2, "facts": {}}))
        for index in range(padding_members):
            if padding_bytes:
                # Stored, not deflated, so the archive is genuinely large on disk.
                archive.writestr(
                    f"CIK{index + 10:010d}.json",
                    "x" * padding_bytes,
                    compress_type=zipfile.ZIP_STORED,
                )
            else:
                archive.writestr(f"CIK{index + 10:010d}.json", json.dumps({"padding": index}))
    return buffer.getvalue()


def test_zip_member_is_read_through_bounded_ranges_and_crc_verified():
    bridge = FixtureBridge()
    payload = _archive()
    bridge.put("research", "sec/bulk/source/companyfacts.zip", payload)
    member, evidence = open_zip_member(
        bridge,
        "research",
        "sec/bulk/source/companyfacts.zip",
        "CIK0000000002.json",
        max_member_bytes=1024,
    )
    assert json.loads(member)["cik"] == 2
    assert (
        evidence["member_crc32"]
        == f"{zipfile.ZipFile(io.BytesIO(payload)).getinfo('CIK0000000002.json').CRC:08x}"
    )
    assert evidence["member_sha256"] == hashlib.sha256(member).hexdigest()
    assert evidence["ranged_reads"] >= 2
    assert evidence["member_bytes"] < evidence["object_bytes"]


def test_large_archive_reads_only_the_central_directory_and_one_member():
    bridge = FixtureBridge()
    payload = _archive(padding_members=100, padding_bytes=20000)  # ~2 MB of archive
    bridge.put("research", "sec/bulk/source/companyfacts.zip", payload)
    member, evidence = open_zip_member(
        bridge,
        "research",
        "sec/bulk/source/companyfacts.zip",
        "CIK0000000002.json",
        max_member_bytes=1024,
    )
    assert json.loads(member)["cik"] == 2
    assert len(payload) > 2_000_000
    # Selective reads only: a fraction of the archive crosses the bridge.
    assert evidence["ranged_bytes"] < 500_000


def test_zip_member_over_budget_and_missing_member_fail_explicitly():
    bridge = FixtureBridge()
    payload = _archive()
    bridge.put("research", "sec/bulk/source/companyfacts.zip", payload)
    with pytest.raises(TransportError) as error:
        open_zip_member(
            bridge,
            "research",
            "sec/bulk/source/companyfacts.zip",
            "CIK0000000002.json",
            max_member_bytes=4,
        )
    assert error.value.code == "READ_BUDGET_EXCEEDED"
    with pytest.raises(TransportError) as error:
        open_zip_member(
            bridge,
            "research",
            "sec/bulk/source/companyfacts.zip",
            "CIK0000009999.json",
            max_member_bytes=1024,
        )
    assert error.value.code == "ZIP_MEMBER_MISSING"


def test_ranged_reader_is_seekable_and_fails_on_a_changed_object():
    bridge = FixtureBridge()
    bridge.put("research", "big.zip", b"0123456789", etag="v1")
    reader = RangedObjectReader(bridge, "research", "big.zip", size=10, etag="v1")
    reader.seek(3)
    assert reader.read(4) == b"3456"
    assert reader.tell() == 7
    bridge.put("research", "big.zip", b"abcdefghij", etag="v2")
    with pytest.raises(TransportError) as error:
        reader.read(2)
    assert error.value.code == "ETAG_CHANGED"


def test_object_meta_round_trip():
    meta = ObjectMeta(store="financial", path="a.json", bytes=3, etag='"e"')
    assert meta.as_dict() == {"store": "financial", "path": "a.json", "bytes": 3, "etag": '"e"'}
