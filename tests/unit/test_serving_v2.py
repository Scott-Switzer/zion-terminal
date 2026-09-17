from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy" / "cloudflare-query" / "src"))

from serving_v2 import (  # type: ignore
    SCHEMA_VERSION,
    ServingV2Error,
    _select_revisions,
    _serve_row,
    cache_key,
    entity_key,
    begin_telemetry,
    current_pointer_cache,
    load_release,
    telemetry_snapshot,
)


def test_cache_identity_is_release_addressed():
    assert cache_key("release-a", "gold/serving/releases/release-a/a.json") != cache_key("release-b", "gold/serving/releases/release-b/a.json")
    assert entity_key("aapl") == "real_equity_AAPL"


def test_pit_revision_selection_keeps_original_until_amendment():
    rows = [
        {"metric_id": "revenue", "period_type": "annual", "period_start": "2008-01-01", "period_end": "2008-12-31", "available_at": "2009-10-27", "form": "10-K", "value_decimal": "24006000000"},
        {"metric_id": "revenue", "period_type": "annual", "period_start": "2008-01-01", "period_end": "2008-12-31", "available_at": "2010-01-25", "form": "10-K/A", "value_decimal": "24578000000"},
    ]
    before = _select_revisions(rows, datetime(2009, 12, 1, tzinfo=timezone.utc))
    after = _select_revisions(rows, datetime(2010, 2, 1, tzinfo=timezone.utc))
    assert before[0]["value_decimal"] == "24006000000"
    assert after[0]["value_decimal"] == "24578000000"


def test_served_row_preserves_evidence_and_exact_decimal_text():
    row = {"entity_id": "real:equity:AAPL", "instrument_id": "AAPL", "metric_id": "revenue", "value_decimal": "109.330001831054690000000000", "unit": "USD", "period_end": "2015-01-02", "period_type": "annual", "available_at": "2015-01-02T21:00:00Z", "evidence_id": "evidence", "source_record_id": "record", "source_revision_id": "revision", "derivation_type": "reported", "producer_release": "release"}
    served = _serve_row(row, release_id="serving", artifact_path="artifact.json")
    assert served["value"] == "109.330001831054690000000000"
    assert served["evidence_id"] == "evidence"
    assert served["provenance"]["serving_release_id"] == "serving"
    assert SCHEMA_VERSION == "financial-serving-v2"


def test_finished_telemetry_exposes_stage_timings_without_internal_clock():
    from serving_v2 import _mark, finish_telemetry

    begin_telemetry()
    _mark("financial_filter", 0.001)
    stats = finish_telemetry()
    assert stats["timing_ms"]["financial_filter"] == 1.0
    assert stats["timing_ms"]["total_worker_path"] >= 0
    assert "_started" not in stats


def test_request_telemetry_starts_with_uncached_current_contract():
    begin_telemetry()
    stats = telemetry_snapshot()
    assert stats == {"r2_gets": 0, "cache_hits": 0, "cache_misses": 0, "cache_errors": 0, "current_uncached": True, "current_cache_hit": 0, "current_cache_miss": 0, "current_cache_age_ms": 0.0, "current_cache_ttl_ms": 0, "current_r2_get_ms": 0.0, "current_serving_release_id": None, "timing_ms": {}}


def test_isolate_diagnostics_are_lazy_and_explicit():
    from types import SimpleNamespace

    begin_telemetry(SimpleNamespace(DIAGNOSTIC_ISOLATE_TELEMETRY="true"))
    first = telemetry_snapshot()
    begin_telemetry(SimpleNamespace(DIAGNOSTIC_ISOLATE_TELEMETRY="true"))
    second = telemetry_snapshot()
    assert first["isolate_instance_id"]
    assert second["isolate_instance_id"] == first["isolate_instance_id"]
    assert second["isolate_request_seq"] == first["isolate_request_seq"] + 1
    assert second["isolate_first_request_at"] == first["isolate_first_request_at"]
    begin_telemetry()
    assert "isolate_instance_id" not in telemetry_snapshot()


def test_invalid_manifest_hash_is_rejected():
    import asyncio
    import hashlib
    import json

    class Obj:
        def __init__(self, value): self.value = value
        async def arrayBuffer(self): return self.value
        async def text(self): return self.value.decode()

    class Bucket:
        def __init__(self, objects): self.objects = objects
        async def get(self, key): return Obj(self.objects[key]) if key in self.objects else None

    manifest = {"schema_version": SCHEMA_VERSION, "serving_release_id": "release-a", "artifacts": []}
    manifest_raw = json.dumps(manifest).encode()
    env = type("Env", (), {"MARKET_DATA": Bucket({"gold/serving/CURRENT.json": json.dumps({"serving_release_id": "release-a", "manifest_key": "gold/serving/releases/release-a/manifest.json", "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest()}).encode(), "gold/serving/releases/release-a/manifest.json": b"corrupt"})})()
    try:
        asyncio.run(load_release(env))
    except ServingV2Error as exc:
        assert exc.code == "SERVING_MANIFEST_CORRUPT"
    else:
        raise AssertionError("corrupt manifest was accepted")


class FakeObj:
    def __init__(self, value): self.value = value

    async def text(self): return self.value.decode()


class FakeBucket:
    def __init__(self, objects): self.objects = objects; self.gets = 0

    async def get(self, key):
        self.gets += 1
        return FakeObj(self.objects[key]) if key in self.objects else None


def make_current(release_id: str) -> bytes:
    import hashlib
    import json

    return json.dumps({"serving_release_id": release_id, "manifest_key": f"gold/serving/releases/{release_id}/manifest.json"}).encode()


def pointer_env(objects: dict, ttl: object = 0):
    bucket = FakeBucket(objects)
    return type("Env", (), {"MARKET_DATA": bucket, "SERVING_V2_CURRENT_KEY": "gold/serving/CURRENT.json", "CURRENT_POINTER_CACHE_TTL_MS": ttl})(), bucket


def run_pointer_case(coro_factory):
    import asyncio

    return asyncio.run(coro_factory())


def pointer_store():
    store = current_pointer_cache()
    store.reset_for_tests()
    return store


def test_pointer_cache_disabled_reads_current_every_request():
    store = pointer_store()
    env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("release-a")}, ttl=0)

    async def case():
        begin_telemetry()
        first = await store.resolve(env)
        second = await store.resolve(env)
        return first, second, bucket.gets, telemetry_snapshot()

    first, second, gets, stats = run_pointer_case(case)
    assert first["serving_release_id"] == "release-a"
    assert second["serving_release_id"] == "release-a"
    assert gets == 2
    assert stats["current_cache_hit"] == 0
    assert stats["current_cache_miss"] == 2


def test_pointer_cache_hit_within_ttl_then_refresh_after_expiry():
    store = pointer_store()
    env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("release-a")}, ttl=1000)
    now = [1000.0]

    async def case():
        begin_telemetry()
        first = await store.resolve(env, now=lambda: now[0])
        now[0] += 0.5
        second = await store.resolve(env, now=lambda: now[0])
        now[0] += 0.6
        third = await store.resolve(env, now=lambda: now[0])
        return first, second, third, bucket.gets, telemetry_snapshot()

    first, second, third, gets, stats = run_pointer_case(case)
    assert first["serving_release_id"] == "release-a"
    assert second["serving_release_id"] == "release-a"
    assert third["serving_release_id"] == "release-a"
    assert gets == 2
    assert stats["current_cache_hit"] == 1
    assert stats["current_cache_miss"] == 2
    assert stats["current_cache_ttl_ms"] == 1000


def test_pointer_cache_uses_seconds_clock_for_one_and_250_ms_ttls():
    for ttl_ms, hit_delta_s, expiry_delta_s in ((1, 0.0005, 0.0015), (250, 0.100, 0.251)):
        store = pointer_store()
        env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("release-a")}, ttl=ttl_ms)
        now = [10.0]

        async def case():
            begin_telemetry()
            await store.resolve(env, now=lambda: now[0])
            now[0] += hit_delta_s
            await store.resolve(env, now=lambda: now[0])
            now[0] += expiry_delta_s - hit_delta_s
            await store.resolve(env, now=lambda: now[0])
            return bucket.gets

        assert run_pointer_case(case) == 2


def test_pointer_cache_reports_age_in_milliseconds():
    store = pointer_store()
    env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("release-a")}, ttl=1000)
    now = [100.0]

    async def case():
        begin_telemetry()
        await store.resolve(env, now=lambda: now[0])
        now[0] += 0.5
        await store.resolve(env, now=lambda: now[0])
        return telemetry_snapshot()

    stats = run_pointer_case(case)
    assert stats["current_cache_hit"] == 1
    assert stats["current_cache_age_ms"] == 500.0


def test_pointer_cache_promotion_and_rollback_obey_hard_ttl():
    store = pointer_store()
    objects = {"gold/serving/CURRENT.json": make_current("release-a")}
    env, bucket = pointer_env(objects, ttl=1000)
    now = [0.0]

    async def case():
        begin_telemetry()
        await store.resolve(env, now=lambda: now[0])
        objects["gold/serving/CURRENT.json"] = make_current("release-b")
        now[0] += 0.5
        before_expiry = await store.resolve(env, now=lambda: now[0])
        now[0] += 0.6
        after_expiry = await store.resolve(env, now=lambda: now[0])
        objects["gold/serving/CURRENT.json"] = make_current("release-a")
        now[0] += 0.5
        rollback_before = await store.resolve(env, now=lambda: now[0])
        now[0] += 0.6
        rollback_after = await store.resolve(env, now=lambda: now[0])
        return before_expiry, after_expiry, rollback_before, rollback_after

    before_expiry, after_expiry, rollback_before, rollback_after = run_pointer_case(case)
    assert before_expiry["serving_release_id"] == "release-a"
    assert after_expiry["serving_release_id"] == "release-b"
    assert rollback_before["serving_release_id"] == "release-b"
    assert rollback_after["serving_release_id"] == "release-a"


def test_pointer_cache_preserves_existing_failure_semantics_on_expired_refresh():
    store = pointer_store()
    env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("release-a")}, ttl=50)
    now = [0.0]

    async def case():
        begin_telemetry()
        await store.resolve(env, now=lambda: now[0])
        bucket.objects.clear()
        now[0] += 0.1
        await store.resolve(env, now=lambda: now[0])

    try:
        run_pointer_case(case)
    except ServingV2Error as exc:
        assert exc.code == "SERVING_ARTIFACT_NOT_FOUND"
    else:
        raise AssertionError("expired refresh missing CURRENT did not fail explicitly")


def test_pointer_cache_never_caches_invalid_pointer():
    store = pointer_store()
    env, bucket = pointer_env({"gold/serving/CURRENT.json": b"not-json"}, ttl=1000)

    async def case():
        begin_telemetry()
        await store.resolve(env)

    try:
        run_pointer_case(case)
    except ServingV2Error as exc:
        assert exc.code == "SERVING_POINTER_INVALID"
    else:
        raise AssertionError("invalid pointer was accepted")

    env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("")}, ttl=1000)

    async def case():
        begin_telemetry()
        await store.resolve(env)

    try:
        run_pointer_case(case)
    except ServingV2Error as exc:
        assert exc.code == "SERVING_POINTER_INVALID"
    else:
        raise AssertionError("pointer without release identity was accepted")


def test_pointer_cache_invalid_ttl_fails_safely_to_disabled():
    store = pointer_store()
    env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("release-a")}, ttl="not-a-number")

    async def case():
        begin_telemetry()
        await store.resolve(env)
        await store.resolve(env)
        return bucket.gets

    assert run_pointer_case(case) == 2


def test_pointer_cache_identity_is_namespace_scoped():
    store = pointer_store()
    env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("release-a")}, ttl=1000)

    assert store.identity(env) == ("gold/serving/CURRENT.json",)

    env_alt = type("Env", (), {"SERVING_V2_CURRENT_KEY": "gold/serving/OTHER.json", "CURRENT_POINTER_CACHE_TTL_MS": 1000})()
    assert store.identity(env_alt) == ("gold/serving/OTHER.json",)


def test_pointer_cache_single_flight_refreshes_once():
    store = pointer_store()
    env, bucket = pointer_env({"gold/serving/CURRENT.json": make_current("release-a")}, ttl=50)
    now = [0.0]

    async def case():
        import asyncio

        begin_telemetry()
        await store.resolve(env, now=lambda: now[0])
        now[0] += 0.1
        first, second = await asyncio.gather(store.resolve(env, now=lambda: now[0]), store.resolve(env, now=lambda: now[0]))
        return first, second, bucket.gets

    first, second, gets = run_pointer_case(case)
    assert first["serving_release_id"] == "release-a"
    assert second["serving_release_id"] == "release-a"
    assert gets == 2


def test_fundamentals_uses_single_release_lineage_per_request():
    import asyncio
    import hashlib
    import json

    from serving_v2 import _cached_text, fundamentals

    store = pointer_store()
    rows = [{"entity_id": "real:equity:AAPL", "metric_id": "revenue", "value_decimal": "1", "period_end": "2024-12-31", "period_type": "annual", "period_start": "2024-01-01", "available_at": "2025-01-01"}]
    annual = json.dumps(rows).encode()
    snapshot = json.dumps({"entity_id": "real:equity:AAPL", "symbol": "AAPL"}).encode()
    manifest_raw = json.dumps({"schema_version": SCHEMA_VERSION, "serving_release_id": "release-a", "artifacts": [{"path": "entities/real_equity_AAPL/snapshot.json", "sha256": hashlib.sha256(snapshot).hexdigest()}, {"path": "entities/real_equity_AAPL/fundamentals/annual.json", "sha256": hashlib.sha256(annual).hexdigest()}], "source": {"fundamentals": {"snapshot_id": 1}, "prices": {"snapshot_id": 2}}}).encode()
    objects = {
        "gold/serving/CURRENT.json": make_current("release-a"),
        "gold/serving/releases/release-a/manifest.json": manifest_raw,
        "gold/serving/releases/release-a/entities/real_equity_AAPL/snapshot.json": snapshot,
        "gold/serving/releases/release-a/entities/real_equity_AAPL/fundamentals/annual.json": annual,
    }
    env, bucket = pointer_env(objects, ttl=1000)
    now = [0.0]

    async def case():
        begin_telemetry()
        await store.resolve(env, now=lambda: now[0])
        return await fundamentals(env, "AAPL", ["revenue"], period="annual", lookback=1)

    # Artifacts resolve through the same pointer identity that fundamentals uses.
    assert run_pointer_case(case)["release"]["serving_release_id"] == "release-a"
    assert _cached_text is not None and hashlib is not None and json is not None
