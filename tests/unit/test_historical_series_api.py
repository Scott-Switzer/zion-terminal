import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from historical_series_api import handle

from zion_terminal.history_store import build_release


class Object:
    def __init__(self, raw):
        self.raw = raw
        self.size = len(raw)

    async def text(self):
        return self.raw.decode()


class Bucket:
    def __init__(self, objects):
        self.objects = objects
        self.reads = []

    async def get(self, key):
        self.reads.append(key)
        return Object(self.objects[key]) if key in self.objects else None


def fixture(tmp_path):
    rows = [
        dict(
            series_id="treasury:10y",
            observation_date="2000-01-0" + str(i),
            value_decimal="2.50" if i != 4 else None,
            missing_reason=None if i != 4 else "SOURCE_NA",
            unit="percent",
            asset_class="rates",
            label="10 year yield",
            source_id="source",
        )
        for i in (3, 4, 5)
    ]
    source = {
        "source": dict(
            url="https://home.treasury.gov/file",
            source_sha256="a" * 64,
            retrieved_at="2026-10-19T00:00:00Z",
        )
    }
    reference = build_release(rows, source, tmp_path)
    manifest = json.loads((tmp_path / reference["relative_manifest"]).read_bytes())
    objects = {reference["key"]: (tmp_path / reference["relative_manifest"]).read_bytes()}
    objects.update(
        {e["key"]: (tmp_path / e["local_path"]).read_bytes() for e in manifest["series"].values()}
    )
    return SimpleNamespace(
        MARKET_DATA=Bucket(objects),
        ZION_PRIVATE_API_TOKEN="test-token",
        HISTORICAL_SERIES_KEY=reference["key"],
        HISTORICAL_SERIES_SHA256=reference["sha256"],
    )


def query(env, params=None, token="test-token", method="GET"):
    return asyncio.run(
        handle(
            env,
            SimpleNamespace(
                url="https://test/private/v1/historical-series?" + urlencode(params or {}),
                headers={"authorization": "Bearer " + token},
                method=method,
            ),
        )
    )


def test_catalog_paging_preserves_missing_lineage_and_query_pin(tmp_path):
    env = fixture(tmp_path)
    catalog, status = query(env)
    assert status == 200 and catalog["series_count"] == 1
    assert len(env.MARKET_DATA.reads) == 1
    first, status = query(env, {"series_id": "treasury:10y", "limit": 2})
    assert status == 200 and first["observations"][1]["value_decimal"] is None
    assert first["observations"][1]["missing_reason"] == "SOURCE_NA"
    assert first["point_in_time_status"] == "NOT_ESTABLISHED"
    assert first["observations"][0]["available_at"] is None
    assert first["source_lineage"]["source"]["source_sha256"] == "a" * 64
    second, status = query(
        env,
        {
            "series_id": "treasury:10y",
            "limit": 2,
            "cursor": first["next_cursor"],
            "expected_release_id": first["release_id"],
        },
    )
    assert status == 200 and len(second["observations"]) == 1 and second["next_cursor"] is None
    bad, status = query(
        env,
        {
            "series_id": "treasury:10y",
            "limit": 1,
            "cursor": first["next_cursor"],
            "expected_release_id": first["release_id"],
        },
    )
    assert status == 409


def test_auth_integrity_and_historical_availability_fail_closed(tmp_path):
    env = fixture(tmp_path)
    assert query(env, token="wrong")[1] == 401 and not env.MARKET_DATA.reads
    assert query(env, method="POST")[1] == 405
    assert query(env, {"as_of": "2000-01-01"})[1] == 422
    assert query(env, {"series_id": "../../private"})[1] == 400
    assert query(env, {"expected_release_id": "b" * 64})[1] == 409
    key = env.HISTORICAL_SERIES_KEY
    env.MARKET_DATA.objects[key] += b" "
    assert query(env)[1] == 503


def coverage_fixture(tmp_path):
    import hashlib

    sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
    from publish_historical_coverage import build_coverage

    env = fixture(tmp_path)
    ref = env.HISTORICAL_SERIES_KEY.removeprefix("gold/historical-series/")
    side = build_coverage(tmp_path, ref, env.HISTORICAL_SERIES_SHA256)
    raw = json.dumps(side, sort_keys=True, separators=(",", ":")).encode()
    sha = hashlib.sha256(raw).hexdigest()
    env.HISTORICAL_COVERAGE_KEY = "gold/historical-series/coverage/" + sha + ".json"
    env.HISTORICAL_COVERAGE_SHA256 = sha
    env.MARKET_DATA.objects[env.HISTORICAL_COVERAGE_KEY] = raw
    return env


def test_coverage_valid_window_and_gaps_in_catalog_and_series(tmp_path):
    env = coverage_fixture(tmp_path)
    catalog, status = query(env)
    assert status == 200
    c = catalog["series"][0]["coverage"]
    assert c["valid_date_min"] == "2000-01-03"
    assert c["valid_date_max"] == "2000-01-05"
    assert c["populated_cells"] == 2 and c["interior_missing"] == 1
    assert c["before_first_value"] == c["after_last_value"] == 0
    response, status = query(env, {"series_id": "treasury:10y"})
    assert status == 200 and response["coverage"] == c
    assert response["coverage_denominator"] == "STORED_SOURCE_CELLS_NOT_EXPECTED_CALENDAR"


def test_coverage_refuses_hash_failure_wrong_release_and_stale_packet(tmp_path):
    import hashlib

    for mutation in (
        "hash",
        "release",
        "packet",
        "counts",
        "manifest",
        "prefix",
        "window",
        "summary",
        "denominator",
        "partial_config",
    ):
        env = coverage_fixture(tmp_path)
        key = env.HISTORICAL_COVERAGE_KEY
        side = json.loads(env.MARKET_DATA.objects[key])
        if mutation == "hash":
            env.MARKET_DATA.objects[key] += b" "
        elif mutation == "partial_config":
            del env.HISTORICAL_COVERAGE_SHA256
        elif mutation == "prefix":
            env.HISTORICAL_COVERAGE_KEY = env.HISTORICAL_SERIES_KEY
        else:
            if mutation == "release":
                side["release_id"] = "b" * 64
            elif mutation == "packet":
                side["series"]["treasury:10y"]["packet_sha256"] = "b" * 64
            elif mutation == "manifest":
                side["manifest_sha256"] = "b" * 64
            elif mutation == "window":
                side["series"]["treasury:10y"]["valid_date_min"] = "1900-01-01"
            elif mutation == "summary":
                side["summary"]["interior_missing"] = 0
            elif mutation == "denominator":
                side["denominator"] = "CALENDAR_COMPLETENESS"
            else:
                side["series"]["treasury:10y"]["populated_cells"] = 1
            raw = json.dumps(side, sort_keys=True, separators=(",", ":")).encode()
            sha = hashlib.sha256(raw).hexdigest()
            env.HISTORICAL_COVERAGE_KEY = "gold/historical-series/coverage/" + sha + ".json"
            env.HISTORICAL_COVERAGE_SHA256 = sha
            env.MARKET_DATA.objects[env.HISTORICAL_COVERAGE_KEY] = raw
        assert query(env)[1] == 503, mutation
