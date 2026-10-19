"""Provider-qualified stored observations; no canonical security or adjustment inference."""

from decimal import Decimal, InvalidOperation

from crsp_archive import digest, encode

PREFIX = "gold/provider-stock-history/releases/"
SCHEMA = "zion-provider-stock-history-v1"
RIGHTS = "PRIVATE_OWNER_STORED_PROVIDER_RESEARCH_ONLY"
COLUMNS = (
    "security_id,price_date,open_price,high_price,low_price,close_price,"
    "adjusted_close_price,volume,source_name,source_id"
)


def identity(provider, security_id):
    return provider + ":" + str(security_id)


def spans(ids, dates, provider, source):
    result = []
    for offset, (sid, native_day) in enumerate(zip(ids, dates)):
        day = str(native_day)[:10]
        key = identity(provider, sid)
        if result and result[-1]["identity"] == key and result[-1]["year"] == day[:4]:
            r = result[-1]
            r["rows"] += 1
            r["date_min"] = min(r["date_min"], day)
            r["date_max"] = max(r["date_max"], day)
        else:
            result.append(
                dict(
                    identity=key,
                    security_id=str(sid),
                    provider=provider,
                    source=source,
                    offset=offset,
                    rows=1,
                    year=day[:4],
                    date_min=day,
                    date_max=day,
                )
            )
    return result


def point(row, provider):
    def value(key):
        v = row.get(key)
        if v is None or isinstance(v, dict):
            return None
        try:
            return str(v) if Decimal(str(v)).is_finite() else None
        except InvalidOperation:
            return None

    return dict(
        security_id=str(row["security_id"]),
        provider=provider,
        session_date=str(row["price_date"])[:10],
        open=value("open_price"),
        high=value("high_price"),
        low=value("low_price"),
        close=value("close_price"),
        adjusted_close=value("adjusted_close_price"),
        volume=value("volume"),
        price_basis="UNKNOWN_FOR_STORED_ROWS",
        adjusted_close_basis="NATIVE_STORED_FIELD_UNCERTIFIED",
        adjustment_method="NOT_DERIVED",
        available_at=None,
        currency=None,
    )


def build(entries, sources):
    shards = {}
    for key, entry in entries.items():
        shards.setdefault(digest(key.encode())[:2], {})[key] = entry
    raws = {s: encode(dict(schema_version=SCHEMA, securities=e)) for s, e in shards.items()}
    root = dict(
        schema_version=SCHEMA,
        rights=RIGHTS,
        source_bucket="financial-system-datasets",
        sources=sources,
        catalog=[{k: v for k, v in e.items() if k != "fragments"} for e in entries.values()],
        indexed_rows=sum(e["normalized_rows"] for e in entries.values()),
        security_count=len(entries),
        price_basis="UNKNOWN_FOR_STORED_ROWS",
        historical_identity="NATIVE_ARCHIVE_ID_ONLY",
        historical_availability="NOT_ESTABLISHED",
        shards={s: dict(sha256=digest(raw), bytes=len(raw)) for s, raw in raws.items()},
    )
    release = digest(encode(root))
    prefix = PREFIX + release + "/"
    objects = {prefix + "shards/" + s + ".json": raw for s, raw in raws.items()}
    for s, ref in root["shards"].items():
        ref["key"] = prefix + "shards/" + s + ".json"
    key = prefix + "manifest.json"
    objects[key] = encode(dict(root, release_id=release))
    return objects, dict(
        key=key, sha256=digest(objects[key]), release_id=release, bucket="financial-system-datasets"
    )
