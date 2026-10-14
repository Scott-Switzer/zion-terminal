import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from sec_history import VERSION, encoded, normalize_sources
from sec_history_api import handle

sys.path.insert(0, str(Path(__file__).parent))
from test_sec_history import fact, source


def fixture():
    data = normalize_sources(source([fact()]), "0000320193", "a" * 64)
    data.update(
        release_id="b" * 64, registry_sha256="c" * 64, source_lineage={"rights": "PUBLIC_SEC_ONLY"}
    )
    body = encoded(data)
    prefix = "gold/sec-financial-history/releases/" + "b" * 64 + "/"
    ref = {
        "key": prefix + "0000320193.json",
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }
    manifest = {
        "schema_version": VERSION,
        "release_id": "b" * 64,
        "registry_sha256": "c" * 64,
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "maximum_as_of": "2022-01-01T00:00:00Z",
        "date_from": "2015-01-01",
        "date_to": "2022-01-01",
        "companies": {"0000320193": ref},
    }
    root = encoded(manifest)

    class Bucket:
        def __init__(self):
            self.reads = 0
            self.corrupt = False

        async def get(self, key):
            self.reads += 1
            raw = root if key.endswith("manifest.json") else body
            if self.corrupt:
                raw = b"{}"

            async def text():
                return raw.decode()

            return SimpleNamespace(size=len(raw), text=text)

    bucket = Bucket()
    env = SimpleNamespace(
        MARKET_DATA=bucket,
        SEC_HISTORY_MANIFEST_KEY=prefix + "manifest.json",
        SEC_HISTORY_MANIFEST_SHA256=hashlib.sha256(root).hexdigest(),
        ZION_PRIVATE_API_TOKEN="owned-test-token",
    )
    return env, bucket


def req(query="", token="owned-test-token", method="GET"):
    return SimpleNamespace(
        url="https://private/private/v1/financial-history?cik=0000320193&metrics=revenue" + query,
        method=method,
        headers={"authorization": "Bearer " + token},
    )


def test_private_history_is_source_pinned_and_complete_lineage():
    env, b = fixture()
    r, status = asyncio.run(handle(env, req()))
    assert status == 200, r
    assert r["observations"][0]["value_decimal"] == "100"
    assert r["source_records"]
    assert all(isinstance(x["source_record"]["val"], str) for x in r["source_records"].values())
    assert r["provenance"]["licensed_sources_used"] is False


def test_unauthorized_and_writes_fail_before_data_access():
    env, b = fixture()
    for request in [req(token="bad"), req(method="POST")]:
        _, status = asyncio.run(handle(env, request))
        assert status in (401, 405)
    assert b.reads == 0


def test_bad_identifier_metrics_dates_sql_and_release_are_rejected():
    env, b = fixture()
    for suffix in [
        "&sql=SELECT+1",
        "&recipient=llm",
        "&expected_release_id=wrong",
        "&as_of=2025-01-01T00:00:00Z",
        "&date_from=invalid",
        "&frequency=weekly",
    ]:
        _, status = asyncio.run(handle(env, req(suffix)))
        assert status in (400, 403, 409, 422)
    b.corrupt = True
    _, status = asyncio.run(handle(env, req()))
    assert status == 409
