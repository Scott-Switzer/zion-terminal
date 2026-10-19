"""Owner-only historical observations from the existing R2 data plane.

No upstream provider calls, arbitrary object access, estimates, or implied vintage eligibility.
"""

import base64
import hashlib
import json
import re
from datetime import date
from urllib.parse import parse_qs, urlparse

from private_financials import PrivateFinancialsError
from private_http import authenticate

PREFIX = "gold/historical-series/releases/"
SCHEMA = "zion-historical-series-v1"
MAX_BYTES = 1048576
ALLOWED = {
    "series_id",
    "asset_class",
    "search",
    "limit",
    "cursor",
    "expected_release_id",
    "date_from",
    "date_to",
    "view",
    "as_of",
}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fail(code, status=400):
    raise PrivateFinancialsError(code, code, status)


async def verified(env, key, sha):
    if not re.fullmatch(
        PREFIX + r"[a-f0-9]{64}/(?:manifest|series/[a-f0-9]{64})\.json", str(key)
    ) or not re.fullmatch(r"[a-f0-9]{64}", str(sha)):
        fail("HISTORY_REFERENCE_INVALID", 503)
    obj = await env.MARKET_DATA.get(key)
    if not obj or obj.size > MAX_BYTES:
        fail("HISTORY_OBJECT_UNAVAILABLE", 503)
    raw = (await obj.text()).encode()
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != sha:
        fail("HISTORY_INTEGRITY_FAILURE", 503)
    value = json.loads(raw)
    if value.get("schema_version") != SCHEMA:
        fail("HISTORY_SCHEMA_INVALID", 503)
    return value


def iso(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        fail("INVALID_DATE")
    try:
        date.fromisoformat(value)
    except ValueError:
        fail("INVALID_DATE")
    return value


async def execute(env, request):
    authenticate(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    query = parse_qs(urlparse(request.url).query, keep_blank_values=True)
    if set(query) - ALLOWED or any(len(v) != 1 for v in query.values()):
        fail("INVALID_PARAMETER")
    p = {k: v[0] for k, v in query.items()}
    if "as_of" in p:
        fail("HISTORICAL_AVAILABILITY_NOT_ESTABLISHED", 422)
    if p.get("view", "catalog") not in ("catalog", "sources"):
        fail("INVALID_VIEW")
    series = p.get("series_id")
    if series and not re.fullmatch(r"[A-Za-z0-9_:.-]{1,180}", series):
        fail("INVALID_SERIES")
    if len(p.get("search", "")) > 128 or len(p.get("asset_class", "")) > 64:
        fail("INVALID_FILTER")
    if not re.fullmatch(r"\d{1,3}", p.get("limit", "100")):
        fail("INVALID_LIMIT")
    limit = int(p.get("limit", "100"))
    if not 1 <= limit <= 500:
        fail("INVALID_LIMIT")
    start = iso(p.get("date_from", "0001-01-01"))
    end = iso(p.get("date_to", "9999-12-31"))
    if start > end:
        fail("INVALID_DATE_RANGE")
    key = getattr(env, "HISTORICAL_SERIES_KEY", None)
    root = await verified(env, key, getattr(env, "HISTORICAL_SERIES_SHA256", None))
    release = root["release_id"]
    prefix = PREFIX + release + "/"
    if key != prefix + "manifest.json":
        fail("HISTORY_REFERENCE_INVALID", 503)
    if p.get("expected_release_id", release) != release:
        fail("HISTORY_RELEASE_CHANGED", 409)
    fingerprint = hashlib.sha256(
        encoded({k: v for k, v in p.items() if k not in ("cursor", "expected_release_id")})
    ).hexdigest()
    offset = 0
    if p.get("cursor"):
        if len(p["cursor"]) > 1000:
            fail("INVALID_CURSOR")
        try:
            cursor = json.loads(base64.urlsafe_b64decode(p["cursor"]))
            if (
                p.get("expected_release_id") != release
                or cursor["release"] != release
                or cursor["query"] != fingerprint
            ):
                fail("HISTORY_CURSOR_CHANGED", 409)
            offset = cursor["offset"]
            if type(offset) is not int or not 0 <= offset <= 100000:
                fail("INVALID_CURSOR")
        except PrivateFinancialsError:
            raise
        except Exception:
            fail("INVALID_CURSOR")
    result = {
        k: root[k]
        for k in (
            "schema_version",
            "release_id",
            "series_count",
            "observation_count",
            "missing_count",
            "point_in_time_status",
            "historical_as_of_supported",
            "availability_policy",
            "rights",
        )
    }
    if p.get("view") == "sources":
        result.update(sources=root["sources"], discovery=root.get("discovery", []))
        return result
    if series:
        if series not in root["series"]:
            fail("SERIES_NOT_FOUND", 404)
        entry = root["series"][series]
        if not entry["key"].startswith(prefix + "series/"):
            fail("HISTORY_REFERENCE_INVALID", 503)
        packet = await verified(env, entry["key"], entry["sha256"])
        if packet.get("series_id") != series:
            fail("HISTORY_SERIES_MISMATCH", 503)
        selected = [r for r in packet["rows"] if start <= r[0] <= end]
        rows = selected[offset : offset + limit]
        result.update(
            series_id=series,
            unit=entry["unit"],
            frequency=entry["frequency"],
            label=entry["label"],
            asset_class=entry["asset_class"],
            total_matches=len(selected),
            observations=[
                dict(
                    observation_date=r[0],
                    value_decimal=r[1],
                    missing_reason=r[2],
                    source_id=r[3],
                    available_at=None,
                )
                for r in rows
            ],
            source_lineage={r[3]: root["sources"][r[3]] for r in rows},
        )
    else:
        selected = [
            dict(
                series_id=k,
                **{
                    field: v[field]
                    for field in (
                        "label",
                        "asset_class",
                        "unit",
                        "frequency",
                        "date_min",
                        "date_max",
                        "observations",
                        "missing",
                    )
                },
            )
            for k, v in sorted(root["series"].items())
            if (not p.get("asset_class") or v["asset_class"] == p["asset_class"])
            and (not p.get("search") or p["search"].lower() in (k + " " + v["label"]).lower())
        ]
        result.update(total_matches=len(selected), series=selected[offset : offset + limit])
    result["next_cursor"] = (
        base64.urlsafe_b64encode(
            encoded(dict(release=release, query=fingerprint, offset=offset + limit))
        ).decode()
        if offset + limit < len(selected)
        else None
    )
    return result


async def handle(env, request):
    try:
        return await execute(env, request), 200
    except PrivateFinancialsError as error:
        return dict(error=dict(code=error.code)), error.status
    except Exception:
        return dict(error=dict(code="HISTORICAL_SERIES_UNAVAILABLE")), 503
