"""Source-native stock archive indexing: no issuer/price inference or provider acquisition."""

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from new_york_tz import new_york_zone

PREFIX = "gold/stock-history/releases/"
RIGHTS = "PRIVATE_OWNER_ALPACA_RESEARCH_ONLY"
SCHEMA = "zion-stock-history-v1"
FIELDS = {
    "open": "o",
    "high": "h",
    "low": "l",
    "close": "c",
    "volume": "v",
    "vwap": "vw",
    "transactions": "n",
}
NY = new_york_zone()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse_page(raw, symbol):
    payload = json.loads(raw, parse_float=Decimal)
    if not isinstance(payload.get("bars"), dict):
        raise ValueError("SOURCE_SCHEMA")
    return parse_bars(payload["bars"].get(symbol, []))


def parse_bars(bars):
    rows = []
    gaps = []
    for bar in bars:
        timestamp = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("SOURCE_TIMESTAMP")
        day = timestamp.astimezone(NY).date().isoformat()
        values = {k: None if bar.get(v) is None else str(bar[v]) for k, v in FIELDS.items()}
        required = ("open", "high", "low", "close", "volume")
        valid = all(values[k] is not None for k in required)
        if valid:
            try:
                nums = {k: Decimal(v) for k, v in values.items() if v is not None}
                valid = (
                    all(v.is_finite() for v in nums.values())
                    and all(nums[k] > 0 for k in ("open", "high", "low", "close"))
                    and nums["volume"] >= 0
                    and nums["low"] <= min(nums["open"], nums["close"])
                    and nums["high"] >= max(nums["open"], nums["close"])
                )
            except Exception:
                valid = False
        if not valid:
            gaps.append(dict(session_date=day, reason="INVALID_OHLC"))
            continue
        rows.append(dict(session_date=day, source_timestamp=bar["t"], **values))
    return rows, gaps


def build(index, pages, *, inventory_sha256, normalized_rows):
    shards = defaultdict(dict)
    for symbol, entry in sorted(index.items()):
        if any(page not in pages for page in entry["source_pages"]):
            raise ValueError("MISSING_SOURCE_PAGE")
        shards[digest(symbol.encode())[:2]][symbol] = entry
    shard_raw = {
        suffix: encode(dict(schema_version=SCHEMA, symbols=items))
        for suffix, items in shards.items()
    }
    identity = dict(
        schema_version=SCHEMA,
        rights=RIGHTS,
        provider="ALPACA",
        feed="SIP",
        price_basis="RAW",
        adjustment_method="NONE",
        inventory_sha256=inventory_sha256,
        normalized_rows=normalized_rows,
        symbol_count=len(index),
        source_pages=pages,
        catalog=[
            dict(
                symbol=symbol,
                normalized_rows=entry["normalized_rows"],
                date_min=entry["date_min"],
                date_max=entry["date_max"],
                source_pages=len(entry["source_pages"]),
            )
            for symbol, entry in sorted(index.items())
        ],
        shards={k: dict(sha256=digest(v), bytes=len(v)) for k, v in shard_raw.items()},
        historical_identity="NOT_ESTABLISHED",
        historical_availability="NOT_ESTABLISHED",
    )
    release = digest(encode(identity))
    prefix = PREFIX + release + "/"
    objects = {prefix + "shards/" + k + ".json": v for k, v in shard_raw.items()}
    root = dict(identity, release_id=release)
    for suffix, ref in root["shards"].items():
        ref["key"] = prefix + "shards/" + suffix + ".json"
    key = prefix + "manifest.json"
    raw = encode(root)
    objects[key] = raw
    if len(raw) > 1048576 or any(len(b) > 1048576 for b in shard_raw.values()):
        raise ValueError("INDEX_BUDGET")
    return objects, dict(key=key, sha256=digest(raw), release_id=release, symbol_count=len(index))
