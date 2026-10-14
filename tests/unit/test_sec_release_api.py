import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from sec_engine import encoded
from sec_engine_api import handle
from test_sec_engine_api import fixture, request


def promoted_fixture():
    env, bucket, objects = fixture()
    oldref = {"key": env.SEC_ENGINE_MANIFEST_KEY, "sha256": env.SEC_ENGINE_MANIFEST_SHA256}
    current = json.loads(objects[oldref["key"]])
    current.update(
        release_id="d" * 64, source_releases=["b" * 64], previous_releases={"b" * 64: oldref}
    )
    ref = current["shards"]["19"]
    shard = json.loads(objects[ref["key"]])
    shard["0000789019"]["materialized"]["source_release_id"] = "b" * 64
    raw = encoded(shard)
    key = ref["key"] + ".promoted"
    objects[key] = raw
    current["shards"]["19"] = {"key": key, "sha256": hashlib.sha256(raw).hexdigest()}
    key = oldref["key"] + ".promoted"
    raw = encoded(current)
    objects[key] = raw
    env.SEC_ENGINE_MANIFEST_KEY = key
    env.SEC_ENGINE_MANIFEST_SHA256 = hashlib.sha256(raw).hexdigest()
    return env, bucket, objects


def test_approved_inherited_payload_and_previous_release_reproduce():
    env, _, _ = promoted_fixture()
    current, status = asyncio.run(handle(env, request()))
    assert status == 200, current
    old, status = asyncio.run(
        handle(
            env,
            request(
                "/private/v1/financial-history?cik=0000789019&metrics=revenue&release_id="
                + "b" * 64
            ),
        )
    )
    assert status == 200, old
    assert current["observations"] == old["observations"]
    assert current["provenance"]["release_id"] == "d" * 64
    assert old["provenance"]["release_id"] == "b" * 64


def test_unapproved_release_and_unsigned_inherited_context_are_denied():
    env, _, objects = promoted_fixture()
    _, status = asyncio.run(
        handle(env, request("/private/v1/financial-history?cik=0000789019&release_id=" + "e" * 64))
    )
    assert status == 404
    current = json.loads(objects[env.SEC_ENGINE_MANIFEST_KEY])
    current["source_releases"] = []
    raw = encoded(current)
    objects[env.SEC_ENGINE_MANIFEST_KEY] = raw
    env.SEC_ENGINE_MANIFEST_SHA256 = hashlib.sha256(raw).hexdigest()
    _, status = asyncio.run(handle(env, request()))
    assert status == 503


def test_updated_company_ceiling_does_not_raise_other_company_or_screen_cutoff():
    from sec_engine import normalize_sources
    from test_sec_history import fact, source

    env, _, objects = promoted_fixture()
    manifest = json.loads(objects[env.SEC_ENGINE_MANIFEST_KEY])
    ref = manifest["shards"]["19"]
    shard = json.loads(objects[ref["key"]])
    company = source([fact(), fact("2024-01-01", "2024-12-31", 150, "2025-01-10", "new")])
    company["cik"] = 789019
    data = normalize_sources(company, "0000789019", "e" * 64)
    data.update(
        release_id="d" * 64, registry_sha256="c" * 64, source_lineage={"rights": "PUBLIC_SEC_ONLY"}
    )
    raw = encoded(data)
    key = "gold/sec-engine/releases/" + "d" * 64 + "/companies/updated.json"
    objects[key] = raw
    item = shard["0000789019"]
    item["materialized"] = {
        "key": key,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "source_release_id": "d" * 64,
    }
    item.update(maximum_as_of="2025-02-01T00:00:00Z", date_to="2025-02-01")
    raw = encoded(shard)
    objects[ref["key"]] = raw
    manifest["shards"]["19"]["sha256"] = hashlib.sha256(raw).hexdigest()
    raw = encoded(manifest)
    objects[env.SEC_ENGINE_MANIFEST_KEY] = raw
    env.SEC_ENGINE_MANIFEST_SHA256 = hashlib.sha256(raw).hexdigest()
    result, status = asyncio.run(handle(env, request()))
    assert status == 200, result
    assert result["as_of"] == "2025-02-01T00:00:00Z"
    assert any(r["value_decimal"] == "150" for r in result["observations"])
    old, status = asyncio.run(
        handle(
            env,
            request(
                "/private/v1/financial-history?cik=0000789019&metrics=revenue&release_id="
                + "b" * 64
            ),
        )
    )
    assert status == 200 and old["as_of"] == "2025-01-01T00:00:00Z"
    assert all(r["value_decimal"] != "150" for r in old["observations"])
