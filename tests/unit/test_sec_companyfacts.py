"""Companion archive references enforce the same issuer, bytes and rights boundaries."""

import asyncio
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
import sec_companyfacts as companion
from private_financials import PrivateFinancialsError

CIK = "0000320193"
KEY = "sec/bulk/sec-nightly-20260924/companyfacts.zip"


class Archive:
    def __init__(self, raw):
        self.raw = raw
        self.reads = []
        self.etag = "pinned-etag"

    async def head(self, key):
        return SimpleNamespace(size=len(self.raw), etag=self.etag)

    async def get(self, key, options):
        self.reads.append((key, options))
        part = options["range"]
        raw = self.raw[part["offset"] : part["offset"] + part["length"]]

        async def array_buffer():
            return raw

        return SimpleNamespace(etag=self.etag, arrayBuffer=array_buffer)


def fixture(cik=CIK):
    raw = json.dumps({"cik": int(cik), "facts": {}, "entityName": "Test"}).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("CIK" + CIK + ".json", raw)
    with zipfile.ZipFile(stream) as archive:
        info = archive.infolist()[0]
    body = stream.getvalue()
    ref = {
        "storage": "PINNED_SEC_ARCHIVE_MEMBER",
        "key": "metadata-only",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "engine_member_sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "archive_entry": {
            "name": info.filename,
            "offset": info.header_offset,
            "flags": info.flag_bits,
            "compression": info.compress_type,
            "compressed_bytes": info.compress_size,
            "uncompressed_bytes": info.file_size,
            "crc": info.CRC,
        },
    }
    manifest = {
        "members": {CIK: ref},
        "source": {
            "bucket": "financial-data-wrds-private",
            "key": KEY,
            "bytes": len(body),
            "etag": "pinned-etag",
        },
    }
    return SimpleNamespace(SEC_ARCHIVE=Archive(body)), manifest, raw


def test_archive_reference_reads_exact_pinned_bytes_with_etag_ranges():
    env, manifest, raw = fixture()
    result, ref = asyncio.run(companion.member(env, manifest, CIK))
    assert result == json.loads(raw)
    assert ref["sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(env.SEC_ARCHIVE.reads) == 3
    for key, options in env.SEC_ARCHIVE.reads:
        assert key == KEY
        assert options["onlyIf"] == {"etagMatches": "pinned-etag"}


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("archive", "UNAPPROVED_ARCHIVE_MEMBER"),
        ("etag", "SOURCE_VERSION_CHANGED"),
        ("hash", "SOURCE_VERSION_CHANGED"),
        ("oversize", "SOURCE_SIZE_BUDGET_OR_MISSING"),
        ("issuer", "SOURCE_CONTEXT_INVALID"),
        ("storage", "SOURCE_REFERENCE_INVALID"),
        ("member_size", "SOURCE_VERSION_CHANGED"),
    ],
)
def test_archive_reference_rejects_wrong_context_or_bytes(mutation, code):
    env, manifest, _ = fixture("0000789019" if mutation == "issuer" else CIK)
    ref = manifest["members"][CIK]
    if mutation == "archive":
        manifest["source"]["bucket"] = "licensed"
    elif mutation == "etag":
        env.SEC_ARCHIVE.etag = "changed"
    elif mutation == "hash":
        ref["sha256"] = ref["engine_member_sha256"] = "0" * 64
    elif mutation == "oversize":
        ref["archive_entry"]["uncompressed_bytes"] = 16 * 1048576 + 1
    elif mutation == "storage":
        ref["storage"] = "ARBITRARY_SOURCE"
    elif mutation == "member_size":
        ref["bytes"] += 1
    with pytest.raises(PrivateFinancialsError) as error:
        asyncio.run(companion.member(env, manifest, CIK))
    assert error.value.code == code
    if mutation in ("archive", "oversize", "storage"):
        assert env.SEC_ARCHIVE.reads == []


def test_default_companion_member_keeps_existing_immutable_object_path():
    env, manifest, raw = fixture()
    ref = manifest["members"][CIK]
    del ref["storage"]
    ref["key"] = "gold/sec-companyfacts/releases/" + "a" * 64 + "/companies/" + CIK + ".json"
    reads = []

    async def get(key):
        reads.append(key)

        async def text():
            return raw.decode()

        return SimpleNamespace(size=len(raw), text=text)

    env.MARKET_DATA = SimpleNamespace(get=get)
    result, _ = asyncio.run(companion.member(env, manifest, CIK))
    assert result["cik"] == 320193
    assert reads == [ref["key"]]
    assert env.SEC_ARCHIVE.reads == []


def test_archive_reference_reports_actual_range_byte_count():
    env, manifest, _ = fixture()
    original = dict(manifest["members"][CIK])
    _, ref = asyncio.run(companion.member(env, manifest, CIK))
    actual_bytes = sum(options["range"]["length"] for _, options in env.SEC_ARCHIVE.reads)
    assert ref["_archive_bytes_read"] == actual_bytes
    assert manifest["members"][CIK] == original


@pytest.mark.parametrize("field", ["offset", "compressed_bytes"])
def test_archive_reference_refuses_negative_range_budgets_before_storage_reads(field):
    env, manifest, _ = fixture()
    manifest["members"][CIK]["archive_entry"][field] = -1
    with pytest.raises(PrivateFinancialsError) as error:
        asyncio.run(companion.member(env, manifest, CIK))
    assert error.value.code == "SOURCE_REFERENCE_INVALID"
    assert env.SEC_ARCHIVE.reads == []


def test_claims_route_reconciles_archive_bytes_and_reports_range_provenance():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "claims_test_fixtures", Path(__file__).with_name("test_equity_claims_api.py")
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    SOURCE_MANIFEST_KEY = fixtures.SOURCE_MANIFEST_KEY
    SOURCE_MEMBER_KEY = fixtures.SOURCE_MEMBER_KEY
    claims_fixture, request = fixtures.fixture, fixtures.request
    from equity_claims_api import handle

    env, bucket = claims_fixture(cik=CIK)
    raw = bucket.bodies[SOURCE_MEMBER_KEY]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("CIK" + CIK + ".json", raw)
    with zipfile.ZipFile(stream) as archive:
        info = archive.infolist()[0]
    body = stream.getvalue()
    env.SEC_ARCHIVE = Archive(body)
    manifest = json.loads(bucket.bodies[SOURCE_MANIFEST_KEY])
    manifest["source"].update({"key": KEY, "bytes": len(body), "etag": "pinned-etag"})
    manifest["members"][CIK].update(
        {
            "storage": "PINNED_SEC_ARCHIVE_MEMBER",
            "key": KEY,
            "archive_entry": {
                "name": info.filename,
                "offset": info.header_offset,
                "flags": info.flag_bits,
                "compression": info.compress_type,
                "compressed_bytes": info.compress_size,
                "uncompressed_bytes": info.file_size,
                "crc": info.CRC,
            },
        }
    )
    manifest_raw = json.dumps(manifest).encode()
    bucket.bodies[SOURCE_MANIFEST_KEY] = manifest_raw
    env.SEC_COMPANYFACTS_MANIFEST_SHA256 = hashlib.sha256(manifest_raw).hexdigest()
    del bucket.bodies[SOURCE_MEMBER_KEY]
    result, status = asyncio.run(handle(env, request(cik=CIK)))
    assert status == 200, result
    assert result["claims"]["debt"]["value_decimal"] == "100164000000"
    assert (
        result["source_bundle"]["reconciliation"]["status"]
        == "MEMBER_SHA256_AGREES_WITH_ENGINE_PINNED_MEMBER"
    )
    assert result["source_bundle"]["companyfacts_release"]["storage"] == "PINNED_SEC_ARCHIVE_MEMBER"
    assert result["provenance"]["archive_bytes_read_interactively"] == sum(
        options["range"]["length"] for _, options in env.SEC_ARCHIVE.reads
    )
