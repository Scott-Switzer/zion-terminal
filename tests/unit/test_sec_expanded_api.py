import asyncio
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))
from private_financials import PrivateFinancialsError  # noqa: E402
from sec_expanded_api import query_expanded  # noqa: E402


class Obj:
    def __init__(self, raw, etag="archive-etag", metadata=None):
        self.raw, self.size, self.etag = raw, len(raw), etag
        self.customMetadata = metadata or {}

    async def text(self):
        return self.raw.decode()

    async def arrayBuffer(self):
        return self.raw


class Bucket:
    def __init__(self, objects):
        self.objects, self.gets, self.puts = objects, [], []

    async def get(self, key, options=None):
        self.gets.append(key)
        obj = self.objects.get(key)
        if obj and options and "range" in options:
            assert options["onlyIf"]["etagMatches"] == "archive-etag"
            r = options["range"]
            return Obj(obj.raw[r["offset"] : r["offset"] + r["length"]])
        return obj

    async def head(self, key):
        return self.objects.get(key)

    async def put(self, key, raw, options):
        self.puts.append(key)
        self.objects[key] = Obj(raw.encode(), metadata=options["customMetadata"])


def env_fixture():
    spec = importlib.util.spec_from_file_location(
        "index_builder_fixture", ROOT / "scripts/publish_sec_expanded_index.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    raw = json.dumps(
        {
            "cik": 1234,
            "entityName": "Stored Fixture",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2023-01-01",
                                    "end": "2023-12-31",
                                    "val": 100,
                                    "filed": "2024-02-01",
                                    "form": "10-K",
                                    "accn": "fixture-accession",
                                }
                            ]
                        }
                    }
                }
            },
        }
    ).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("CIK0000001234.json", raw)
    archive = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        i = z.infolist()[0]
        entry = {
            "name": i.filename,
            "compression": i.compress_type,
            "flags": i.flag_bits,
            "crc": i.CRC,
            "compressed_bytes": i.compress_size,
            "uncompressed_bytes": i.file_size,
            "offset": i.header_offset,
        }
    source = {
        "bucket": "financial-data-wrds-private",
        "key": "sec/bulk/sec-nightly-20260924/companyfacts.zip",
        "etag": "archive-etag",
        "bytes": len(archive),
        "snapshot_date": "2026-09-24",
        "declared_sha256": "a" * 64,
    }
    objects, metadata = builder.build_objects(
        [entry], {"0": {"cik_str": 1234, "ticker": "NEW"}}, source
    )
    return SimpleNamespace(
        MARKET_DATA=Bucket({k: Obj(v) for k, v in objects.items()}),
        SEC_ARCHIVE=Bucket({source["key"]: Obj(archive)}),
        SEC_EXPANDED_INDEX_KEY=metadata["key"],
        SEC_EXPANDED_INDEX_SHA256=metadata["sha256"],
    )


def query(env, **body):
    return asyncio.run(query_expanded(env, body, request_id="fresh-request"))


def test_expanded_query_reads_one_bounded_member_and_persists_normalized_result():
    env = env_fixture()
    result = query(env, operation="sec_financials", symbol="NEW")
    assert result["data"]["observations"][0]["value"] == "100"
    assert result["entity"]["cik"] == "0000001234"
    assert result["release"]["sec_archive_release_id"]
    assert result["cache"] == "MISS"
    assert env.MARKET_DATA.puts
    reads = len(env.SEC_ARCHIVE.gets)
    second = query(env, operation="sec_financials", symbol="NEW")
    assert second["cache"] == "HIT"
    assert second["request_id"] == "fresh-request"
    assert len(env.SEC_ARCHIVE.gets) == reads
    assert "sec/bulk/" not in json.dumps(result)


def test_coverage_does_not_read_or_extract_the_archive():
    env = env_fixture()
    r = query(env, operation="sec_coverage")
    assert r["data"]["indexed_filers"] == 1
    assert not env.SEC_ARCHIVE.gets
    assert not env.MARKET_DATA.puts


@pytest.mark.parametrize(
    "args",
    [
        {"cik": "../secrets"},
        {"symbol": "UNKNOWN"},
        {"metrics": ["invented"]},
        {"lookback": 41},
        {"symbol": "NEW", "as_of": "2024-01-01T00:00:00Z"},
        {"period": "ttm"},
        {"extra": "bad"},
    ],
)
def test_invalid_or_unsupported_queries_fail_closed(args):
    with pytest.raises(PrivateFinancialsError):
        query(env_fixture(), **({"operation": "sec_financials", "cik": "0000001234"} | args))


def test_source_version_change_and_tampered_cache_are_rejected():
    env = env_fixture()
    next(iter(env.SEC_ARCHIVE.objects.values())).etag = "changed"
    with pytest.raises(PrivateFinancialsError):
        query(env, operation="sec_financials", cik="0000001234")
    env = env_fixture()
    query(env, operation="sec_financials", cik="0000001234")
    env.MARKET_DATA.objects[env.MARKET_DATA.puts[0]].raw = b"{}"
    with pytest.raises(PrivateFinancialsError):
        query(env, operation="sec_financials", cik="0000001234")


def test_cache_identity_tracks_current_request():
    env = env_fixture()
    query(env, operation="sec_financials", symbol="NEW")
    result = query(env, operation="sec_financials", cik="0000001234")
    assert result["cache"] == "HIT"
    assert result["entity"]["requested_symbol"] is None


def test_javascript_null_cache_miss_is_not_a_cached_object():
    class JsNull:
        def __bool__(self):
            return False

    env = env_fixture()
    original = env.MARKET_DATA.get

    async def get(key, options=None):
        result = await original(key, options)
        return JsNull() if result is None else result

    env.MARKET_DATA.get = get
    assert query(env, operation="sec_financials", symbol="NEW")["cache"] == "MISS"
