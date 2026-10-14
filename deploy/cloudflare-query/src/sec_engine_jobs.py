"""Durable bounded SEC tasks. One issuer per message; deterministic immutable results."""

import asyncio
import json
import re
import struct
import uuid
import zlib
from decimal import Decimal
from urllib.parse import urlencode

from sec_engine import normalize_sources
from sec_engine_api import (
    HISTORY_PARAMETERS,
    PREFIX,
    company,
    entry,
    fail,
    financial_history,
    parameters,
    persist,
    root,
    sha,
    stored,
    validate,
)
from sec_expanded_api import js_options


def plain(value):
    return value.to_py() if hasattr(value, "to_py") else value


async def source_member(env, source, e, cik):
    if (
        source.get("key") != "sec/bulk/sec-nightly-20260924/companyfacts.zip"
        or source.get("bucket") != "financial-data-wrds-private"
        or e.get("name") != f"CIK{cik}.json"
    ):
        fail("UNAPPROVED_ARCHIVE_MEMBER", 403)
    if (
        e.get("flags", 0) & 1
        or e["compression"] not in (0, 8)
        or e["uncompressed_bytes"] > 24 * 1048576
        or e["compressed_bytes"] > 8 * 1048576
    ):
        fail("MEMBER_REQUIRES_OPERATOR_BUDGET_REVIEW", 422)
    head = await env.SEC_ARCHIVE.head(source["key"])
    if not head or head.size != source["bytes"] or head.etag.strip('"') != source["etag"]:
        fail("SOURCE_VERSION_CHANGED", 409)

    async def read(offset, length):
        if not 1 <= length <= 1048576 or not 0 <= offset <= source["bytes"] - length:
            fail("SOURCE_RANGE_INVALID", 503)
        obj = await env.SEC_ARCHIVE.get(
            source["key"],
            js_options(
                {
                    "range": {"offset": offset, "length": length},
                    "onlyIf": {"etagMatches": source["etag"]},
                }
            ),
        )
        if not obj or obj.etag.strip('"') != source["etag"]:
            fail("SOURCE_VERSION_CHANGED", 409)
        array = await obj.arrayBuffer()
        raw = bytes(plain(array))
        if len(raw) != length:
            fail("SOURCE_RANGE_LENGTH", 503)
        return raw

    h = struct.unpack("<4s5H3I2H", await read(e["offset"], 30))
    if h[0] != b"PK\x03\x04" or h[2] != e["flags"] or h[3] != e["compression"]:
        fail("ZIP_HEADER_INVALID", 503)
    if (await read(e["offset"] + 30, h[-2])).decode() != e["name"]:
        fail("ZIP_MEMBER_IDENTITY", 503)
    offset = e["offset"] + 30 + h[-2] + h[-1]
    remaining = e["compressed_bytes"]
    parts = []
    while remaining:
        n = min(remaining, 1048576)
        parts.append(await read(offset, n))
        offset += n
        remaining -= n
    compressed = b"".join(parts)
    if e["compression"] == 8:
        decoder = zlib.decompressobj(-15)
        raw = decoder.decompress(compressed, 24 * 1048576 + 1)
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            fail("ZIP_STREAM_INVALID", 503)
    else:
        raw = compressed
    if len(raw) != e["uncompressed_bytes"] or zlib.crc32(raw) != e["crc"]:
        fail("ZIP_CRC_SIZE_INVALID", 503)
    return raw


async def submit(env, m, query, ciks):
    if not getattr(env, "SEC_ENGINE_QUEUE", None):
        fail("ASYNC_QUEUE_NOT_CONFIGURED", 503)
    if not 1 <= len(ciks) <= 20 or len(set(ciks)) != len(ciks):
        fail("JOB_REQUIRES_1_TO_20_UNIQUE_CIKS", 422)
    for cik in ciks:
        validate({**query, "cik": cik}, m)
        await entry(env, m, cik)
    job_id = uuid.uuid4().hex + uuid.uuid4().hex
    record = {
        "job_id": job_id,
        "release_id": m["release_id"],
        "query": query,
        "ciks": ciks,
        "status": "QUEUED",
    }
    key = PREFIX + m["release_id"] + "/jobs/" + job_id + "/request.json"
    await persist(env, key, record, env.ZION_PRIVATE_API_TOKEN)
    # Await durable acceptance. No background floating producer promise.
    await env.SEC_ENGINE_QUEUE.sendBatch(
        js_options(
            [
                {"body": {"job_id": job_id, "release_id": m["release_id"], "cik": cik}}
                for cik in ciks
            ]
        )
    )
    return {
        "status": "QUEUED",
        "job_id": job_id,
        "release_id": m["release_id"],
        "companies": len(ciks),
        "status_url": "/private/v1/financial-jobs?job_id=" + job_id,
        "limits": {
            "max_companies": 20,
            "company_tasks_per_message": 1,
            "retries": 2,
            "consumer_concurrency": 1,
        },
    }, 202


async def jobs_api(env, request):
    m = await root(env)
    if request.method == "POST":
        if int(request.headers.get("content-length", "0")) > 8192:
            fail("JOB_REQUEST_TOO_LARGE", 413)
        raw = await request.text()
        if len(raw.encode()) > 8192:
            fail("JOB_REQUEST_TOO_LARGE", 413)
        body = json.loads(raw)
        if not isinstance(body, dict) or set(body) - (HISTORY_PARAMETERS - {"cik", "cursor"}):
            fail("INVALID_JOB_PARAMETER")
        ciks = body.pop("ciks", [])
        if not isinstance(ciks, list) or any(not isinstance(x, str) for x in ciks):
            fail("INVALID_JOB_CIKS", 422)
        return await submit(env, m, body, ciks)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    q = parameters(request.url, {"job_id"})
    job_id = q.get("job_id", "")
    if not re.fullmatch("[a-f0-9]{64}", job_id):
        fail("INVALID_JOB_ID")
    base = PREFIX + m["release_id"] + "/jobs/" + job_id
    record = await stored(env, base + "/request.json", env.ZION_PRIVATE_API_TOKEN)
    if record is None:
        fail("JOB_NOT_FOUND_IN_CURRENT_RELEASE", 404)
    tasks = []
    for cik in record["ciks"]:
        part = await stored(env, base + "/" + cik + ".json", env.ZION_PRIVATE_API_TOKEN)
        if part is None:
            part = {"status": "QUEUED"}
        tasks.append({"cik": cik, **part})
    done = all(x["status"] in ("COMPLETE", "FAILED", "QUARANTINED") for x in tasks)
    return {
        "job_id": job_id,
        "release_id": m["release_id"],
        "status": "COMPLETE_WITH_FAILURES"
        if done and any(x["status"] != "COMPLETE" for x in tasks)
        else "COMPLETE"
        if done
        else "QUEUED",
        "companies": tasks,
    }, 200


async def consume(env, message):
    m = await root(env)
    message = plain(message)
    if (
        set(message) != {"job_id", "release_id", "cik"}
        or message["release_id"] != m["release_id"]
        or not re.fullmatch("[a-f0-9]{64}", message["job_id"])
        or not re.fullmatch(r"\d{10}", message["cik"])
    ):
        fail("QUEUE_SOURCE_CONTEXT_INVALID", 409)
    base = PREFIX + m["release_id"] + "/jobs/" + message["job_id"]
    cik = message["cik"]
    record = await stored(env, base + "/request.json", env.ZION_PRIVATE_API_TOKEN)
    if not record or cik not in record["ciks"] or record["release_id"] != m["release_id"]:
        fail("QUEUE_JOB_CONTEXT_INVALID", 409)
    existing = await stored(env, base + "/" + cik + ".json", env.ZION_PRIVATE_API_TOKEN)
    if existing and existing["status"] in ("COMPLETE", "FAILED", "QUARANTINED"):
        return
    p = validate({**record["query"], "cik": cik}, m)
    e = await entry(env, m, cik)
    data = await company(env, m, cik, e)
    if data is None:
        raw = await source_member(env, m["source"], e["archive_entry"], cik)
        data = normalize_sources(json.loads(raw, parse_float=Decimal), cik, sha(raw))
        data.update(
            release_id=m["release_id"],
            registry_sha256=m["registry_sha256"],
            source_lineage={
                "rights": "PUBLIC_SEC_ONLY",
                "archive": m["source"],
                "entry": e["archive_entry"],
                "member_sha256": sha(raw),
                "cik": cik,
            },
        )
        if len(data["observations"]) > 12000:
            fail("MATERIALIZATION_RECORD_BUDGET", 413)
        if not any(
            r["metric"] in ("revenue", "net_income", "operating_cash_flow", "diluted_shares")
            for r in data["observations"]
        ):
            await persist(
                env,
                base + "/" + cik + ".json",
                {
                    "status": "QUARANTINED",
                    "reason": "NO_SUPPORTED_US_GAAP_PERIODIC_SOURCE_OBSERVATIONS",
                },
                env.ZION_PRIVATE_API_TOKEN,
            )
            return
        await persist(
            env,
            PREFIX + m["release_id"] + "/computed/" + cik + ".json",
            data,
            env.ZION_PRIVATE_API_TOKEN,
        )
    result, status = await financial_history(env, m, p, allow_queue=False)
    if status != 200:
        fail("ASYNC_QUERY_FAILED", 503)
    query = {
        k: (",".join(v) if isinstance(v, list) else str(v))
        for k, v in p.items()
        if k not in ("cursor", "ciks")
    }
    query["expected_release_id"] = m["release_id"]
    await persist(
        env,
        base + "/" + cik + ".json",
        {
            "status": "COMPLETE",
            "total_observations": result["pagination"]["total_observations"],
            "total_coverage_gaps": result["pagination"]["total_coverage_gaps"],
            "history_url": "/private/v1/financial-history?" + urlencode(query),
            "query_fingerprint": result["cache"]["query_fingerprint"],
        },
        env.ZION_PRIVATE_API_TOKEN,
    )


async def queue_batch(env, batch):
    import traceback

    from private_financials import PrivateFinancialsError

    for message in batch.messages:
        try:
            await asyncio.wait_for(consume(env, message.body), timeout=120)
            message.ack()
        except Exception as ex:
            permanent = isinstance(ex, PrivateFinancialsError) and ex.status in (
                400,
                403,
                404,
                409,
                413,
                422,
            )
            exhausted = int(message.attempts) >= 3
            reason = (
                ex.code
                if isinstance(ex, PrivateFinancialsError)
                else "ASYNC_TASK_" + type(ex).__name__.upper()
            )
            print(
                json.dumps(
                    {
                        "event": "sec_engine_task_failure",
                        "reason": reason,
                        "attempt": int(message.attempts),
                        "frames": [
                            [f.name, f.lineno] for f in traceback.extract_tb(ex.__traceback__)
                        ],
                    }
                )
            )
            if permanent or exhausted:
                body = plain(message.body)
                if (
                    re.fullmatch("[a-f0-9]{64}", str(body.get("job_id", "")))
                    and re.fullmatch(r"\d{10}", str(body.get("cik", "")))
                    and re.fullmatch("[a-f0-9]{64}", str(body.get("release_id", "")))
                ):
                    key = (
                        PREFIX
                        + body["release_id"]
                        + "/jobs/"
                        + body["job_id"]
                        + "/"
                        + body["cik"]
                        + ".json"
                    )
                    await persist(
                        env, key, {"status": "FAILED", "reason": reason}, env.ZION_PRIVATE_API_TOKEN
                    )
                message.ack()
            else:
                message.retry(js_options({"delaySeconds": 30}))
