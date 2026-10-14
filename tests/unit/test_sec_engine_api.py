import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
sys.path.insert(0, str(Path(__file__).parent))
from sec_engine import VERSION, encoded, normalize_sources
from sec_engine_api import handle, page_result
from test_sec_history import fact, source


def fixture():
    prefix = "gold/sec-engine/releases/" + "b" * 64 + "/"
    c = source([fact()])
    c["cik"] = 789019
    data = normalize_sources(c, "0000789019", "a" * 64)
    data.update(
        release_id="b" * 64, registry_sha256="c" * 64, source_lineage={"rights": "PUBLIC_SEC_ONLY"}
    )
    objects = {}

    def pin(name, value):
        body = encoded(value)
        key = prefix + name
        objects[key] = body
        return {"key": key, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}

    company = pin("companies/0000789019.json", data)
    shard = pin(
        "catalog/19.json",
        {
            "0000789019": {
                "materialized": company,
                "label": "Microsoft",
                "archive_entry": {"name": "CIK0000789019.json"},
            }
        },
    )
    directory = pin(
        "directory.json", [{"cik": "0000789019", "label": "Microsoft", "status": "MATERIALIZED"}]
    )
    manifest = {
        "schema_version": VERSION,
        "release_id": "b" * 64,
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "registry_sha256": "c" * 64,
        "date_from": "1930-01-01",
        "date_to": "2025-01-01",
        "maximum_as_of": "2025-01-01T00:00:00Z",
        "shards": {"19": shard},
        "directory": directory,
        "source": {"etag": "owned", "bytes": 1},
    }
    root = pin("manifest.json", manifest)

    class Bucket:
        def __init__(self):
            self.reads = 0
            self.writes = 0

        async def get(self, key, *args):
            self.reads += 1
            if key not in objects:
                return None
            raw = objects[key]

            async def text():
                return raw.decode()

            return SimpleNamespace(size=len(raw), text=text)

        async def put(self, key, body, *args):
            self.writes += 1
            objects[key] = body.encode() if isinstance(body, str) else body
            return SimpleNamespace()

    b = Bucket()
    return (
        SimpleNamespace(
            MARKET_DATA=b,
            ZION_PRIVATE_API_TOKEN="owned",
            SEC_ENGINE_MANIFEST_KEY=root["key"],
            SEC_ENGINE_MANIFEST_SHA256=root["sha256"],
        ),
        b,
        objects,
    )


def request(
    path="/private/v1/financial-history?cik=0000789019&metrics=revenue", token="owned", method="GET"
):
    return SimpleNamespace(
        url="https://owned" + path, method=method, headers={"authorization": "Bearer " + token}
    )


def test_generic_company_live_shape_and_cache_preserve_exact_leaf_values():
    env, b, _ = fixture()
    a, status = asyncio.run(handle(env, request()))
    assert status == 200, a
    assert a["observations"][0]["value_decimal"] == "100"
    assert all(isinstance(x["source_record"]["val"], str) for x in a["source_records"].values())
    cached, status = asyncio.run(handle(env, request()))
    assert status == 200
    assert cached["cache"]["state"] == "HIT"
    assert a["observations"] == cached["observations"]


def test_auth_denies_before_any_source_or_cache_read():
    env, b, _ = fixture()
    _, status = asyncio.run(handle(env, request(token="wrong")))
    assert status == 401 and b.reads == 0 and b.writes == 0


def test_cursor_is_bound_to_query_and_release_and_all_pages_reconstruct():
    full = {
        "observations": [
            {"metric": "revenue", "period_end": str(x), "source_observation_ids": []}
            for x in range(5)
        ],
        "coverage_gaps": [{"metric": "revenue", "reason": "MISSING"}],
        "accounting_checks": [],
        "source_records": {},
    }
    a = page_result(copy.deepcopy(full), "query-a", "b" * 64, 2, None, "owned")
    assert a["pagination"]["total_items"] == 6
    b = page_result(
        copy.deepcopy(full), "query-a", "b" * 64, 2, a["pagination"]["next_cursor"], "owned"
    )
    c = page_result(
        copy.deepcopy(full), "query-a", "b" * 64, 2, b["pagination"]["next_cursor"], "owned"
    )
    assert len(a["observations"] + b["observations"] + c["observations"]) == 5
    assert c["coverage_gaps"] == full["coverage_gaps"] and c["pagination"]["next_cursor"] is None
    import pytest

    with pytest.raises(ValueError):
        page_result(full, "query-other", "b" * 64, 2, a["pagination"]["next_cursor"], "owned")
    with pytest.raises(ValueError):
        page_result(full, "query-a", "c" * 64, 2, a["pagination"]["next_cursor"], "owned")


def test_discovery_and_invalid_company_metrics_sql_are_explicit():
    env, b, _ = fixture()
    d, status = asyncio.run(handle(env, request("/private/v1/financial-companies")))
    assert status == 200 and d["companies"][0]["cik"] == "0000789019"
    for query in [
        "cik=0000789019&sql=SELECT+1",
        "cik=invalid",
        "cik=0000789019&metrics=liabilities_as_debt",
    ]:
        d, status = asyncio.run(handle(env, request("/private/v1/financial-history?" + query)))
        assert status in (400, 422), d
    d, status = asyncio.run(handle(env, request("/private/v1/financial-history?cik=0000000000")))
    assert status == 404 and d["error"]["code"] == "CIK_NOT_IN_SOURCE_ARCHIVE"


def test_corrupted_source_never_uses_query_cache():
    env, b, objects = fixture()
    asyncio.run(handle(env, request()))
    for key in list(objects):
        if key.endswith("companies/0000789019.json"):
            objects[key] = b"{}"
    # Pinned materialized object corruption must be detected before returning cached results.
    d, status = asyncio.run(handle(env, request()))
    assert status == 409, d


def test_invalid_cursor_cannot_skip_lineage_or_change_filters():
    env, b, _ = fixture()
    d, status = asyncio.run(
        handle(
            env,
            request("/private/v1/financial-history?cik=0000789019&metrics=revenue&cursor=forged"),
        )
    )
    assert status == 400 and d["error"]["code"] == "INVALID_CURSOR_OR_PARAMETER"


def test_batch_uses_same_query_contract_for_multiple_companies():
    env, b, objects = fixture()
    d, status = asyncio.run(
        handle(env, request("/private/v1/financial-history/batch?ciks=0000789019&metrics=revenue"))
    )
    assert (
        status == 200 and d["companies"][0]["result"]["observations"][0]["value_decimal"] == "100"
    )


def test_durable_job_replay_does_not_recompute_completed_task():
    from sec_engine_jobs import consume

    env, b, objects = fixture()
    manifest_key = env.SEC_ENGINE_MANIFEST_KEY
    m = json.loads(objects[manifest_key])
    from sec_engine_api import persist

    job_id = "d" * 64
    key = "gold/sec-engine/releases/" + m["release_id"] + "/jobs/" + job_id
    job = {
        "job_id": job_id,
        "release_id": m["release_id"],
        "ciks": ["0000789019"],
        "query": {"metrics": ["revenue"]},
    }
    asyncio.run(persist(env, key + "/request.json", job, env.ZION_PRIVATE_API_TOKEN))
    asyncio.run(
        persist(env, key + "/0000789019.json", {"status": "COMPLETE"}, env.ZION_PRIVATE_API_TOKEN)
    )
    writes = b.writes
    asyncio.run(
        consume(env, {"job_id": job_id, "release_id": m["release_id"], "cik": "0000789019"})
    )
    assert b.writes == writes


def test_unmaterialized_company_queues_bounded_task_instead_of_reading_zip_interactively():
    env, b, objects = fixture()
    for key, raw in list(objects.items()):
        if key.endswith("catalog/19.json"):
            shard = json.loads(raw)
            shard["0000789019"].pop("materialized")
            body = encoded(shard)
            objects[key] = body
            root_key = env.SEC_ENGINE_MANIFEST_KEY
            m = json.loads(objects[root_key])
            m["shards"]["19"]["sha256"] = hashlib.sha256(body).hexdigest()
            objects[root_key] = encoded(m)
            env.SEC_ENGINE_MANIFEST_SHA256 = hashlib.sha256(objects[root_key]).hexdigest()

    class Queue:
        async def sendBatch(self, messages):
            self.messages = messages

    env.SEC_ENGINE_QUEUE = Queue()
    r, status = asyncio.run(handle(env, request()))
    assert status == 202 and r["status"] == "QUEUED"
    assert len(env.SEC_ENGINE_QUEUE.messages) == 1


def test_exhausted_queue_attempt_is_durable_failure_not_permanently_queued(monkeypatch):
    import sec_engine_jobs as jobs
    from sec_engine_api import stored

    env, b, objects = fixture()
    body = {"job_id": "d" * 64, "release_id": "b" * 64, "cik": "0000789019"}

    class Message:
        attempts = 3

        def __init__(self):
            self.body = body
            self.acked = False

        def ack(self):
            self.acked = True

        def retry(self, *args):
            raise AssertionError("must record final failure")

    msg = Message()

    async def broken(*args):
        raise TypeError("sensitive detail must not be logged")

    monkeypatch.setattr(jobs, "consume", broken)
    asyncio.run(jobs.queue_batch(env, SimpleNamespace(messages=[msg])))
    key = "gold/sec-engine/releases/" + "b" * 64 + "/jobs/" + "d" * 64 + "/0000789019.json"
    result = asyncio.run(stored(env, key, env.ZION_PRIVATE_API_TOKEN))
    assert result["status"] == "FAILED" and msg.acked


def test_queue_entry_accepts_actual_runtime_batch_env_context_arguments(monkeypatch):
    import ast

    import sec_engine_jobs

    tree = ast.parse(
        (Path(__file__).parents[2] / "deploy/cloudflare-query/src/thin_worker.py").read_text()
    )
    method = next(
        n
        for c in tree.body
        if isinstance(c, ast.ClassDef) and c.name == "Default"
        for n in c.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "queue"
    )
    namespace = {}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
            "queue_bridge",
            "exec",
        ),
        namespace,
    )
    calls = []

    async def handler(env, batch):
        calls.append((env, batch))

    monkeypatch.setattr(sec_engine_jobs, "queue_batch", handler)
    env, b, _ = fixture()
    batch = SimpleNamespace(messages=[])
    asyncio.run(namespace["queue"](SimpleNamespace(env=env), batch, env, SimpleNamespace()))
    assert calls == [(env, batch)]


def test_quarantined_archive_identity_cannot_be_queued_or_served():
    env, b, objects = fixture()
    key = next(k for k in objects if k.endswith("catalog/19.json"))
    shard = json.loads(objects[key])
    shard["0000789019"] = {"quarantine": {"reason": "SOURCE_IDENTITY_MISMATCH"}}
    objects[key] = encoded(shard)
    m = json.loads(objects[env.SEC_ENGINE_MANIFEST_KEY])
    m["shards"]["19"]["sha256"] = hashlib.sha256(objects[key]).hexdigest()
    objects[env.SEC_ENGINE_MANIFEST_KEY] = encoded(m)
    env.SEC_ENGINE_MANIFEST_SHA256 = hashlib.sha256(
        objects[env.SEC_ENGINE_MANIFEST_KEY]
    ).hexdigest()
    response, status = asyncio.run(handle(env, request()))
    assert status == 422 and response["error"]["code"] == "CIK_QUARANTINED_SOURCE_IDENTITY_MISMATCH"
