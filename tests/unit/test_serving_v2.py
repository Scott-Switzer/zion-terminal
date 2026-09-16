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
    assert stats == {"r2_gets": 0, "cache_hits": 0, "cache_misses": 0, "cache_errors": 0, "current_uncached": True, "timing_ms": {}}


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
