"""Private CRSP metadata index. Stable PERMNO joins and unaltered native semantics."""

import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

SCHEMA = "zion-crsp-stock-index-v1"
PREFIX = "gold/crsp-stock-history/releases/"
RIGHTS = "PRIVATE_OWNER_WRDS_CRSP_RESEARCH_ONLY"


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def catalog(rows):
    result = {}
    for row in rows:
        permno = int(row["permno"])
        entry = result.setdefault(
            str(permno), dict(permno=permno, security_id=f"CRSP:PERMNO:{permno}", intervals=[])
        )
        interval = dict(
            symbol=(row.get("ticker") or "").strip(),
            name=(row.get("comnam") or row.get("issuernm") or row.get("securitynm") or "").strip(),
            valid_from=str(row.get("namedt") or row.get("secinfostartdt"))
            if row.get("namedt") or row.get("secinfostartdt")
            else None,
            valid_to=str(row.get("nameenddt") or row.get("secinfoenddt"))
            if row.get("nameenddt") or row.get("secinfoenddt")
            else None,
            permco=row.get("permco"),
            identity_dataset="stksecurityinfohist" if "secinfostartdt" in row else "stocknames",
        )
        if interval not in entry["intervals"]:
            entry["intervals"].append(interval)
    for entry in result.values():
        entry["intervals"].sort(
            key=lambda x: (x["valid_from"] or "", x["valid_to"] or "", x["symbol"])
        )
        latest = entry["intervals"][-1]
        entry.update(
            symbol=latest["symbol"],
            name=latest["name"],
            identity_basis="NATIVE_DATED_PERMNO_NAME_INTERVALS",
        )
    return result


def search(entries, query, *, as_of=None, limit=50):
    q = query.strip().upper()
    matches = []
    for entry in entries.values():
        intervals = [
            r
            for r in entry["intervals"]
            if not as_of
            or (not r["valid_from"] or r["valid_from"] <= as_of)
            and (not r["valid_to"] or as_of <= r["valid_to"])
        ]
        if any(q in r["symbol"].upper() or q in r["name"].upper() for r in intervals) or q == str(
            entry["permno"]
        ):
            matches.append(entry)
    matches.sort(
        key=lambda e: (not any(r["symbol"].upper() == q for r in e["intervals"]), e["permno"])
    )
    return matches[:limit]


def resolve(entries, *, permno=None, symbol=None, as_of=None):
    if permno is not None:
        entry = entries.get(str(int(permno)))
        if entry is None:
            raise ValueError("CRSP_SECURITY_NOT_FOUND")
        return entry
    matches = [
        e
        for e in search(entries, symbol or "", as_of=as_of, limit=len(entries))
        if any(
            r["symbol"].upper() == (symbol or "").upper()
            and (
                not as_of
                or (not r["valid_from"] or r["valid_from"] <= as_of)
                and (not r["valid_to"] or as_of <= r["valid_to"])
            )
            for r in e["intervals"]
        )
    ]
    if len(matches) != 1:
        raise ValueError("CRSP_AMBIGUOUS_TICKER" if matches else "CRSP_SECURITY_NOT_FOUND")
    return matches[0]


def locators(groups):
    result = defaultdict(list)
    for group in groups:
        for permno, rows in group["permnos"].items():
            result[str(int(permno))].append(
                {k: group[k] for k in ("source", "row_group", "date_min", "date_max")}
                | dict(rows=int(rows))
            )
    return dict(result)


def row_spans(values, *, offset, row_group, source, date_min, date_max, dates=None):
    """Run-length identity/year spans; absolute offsets retain disjoint native runs."""
    result = []
    previous_date = None
    for i, value in enumerate(values):
        permno = int(value)
        day = str(dates[i])[:10] if dates is not None else None
        year = day[:4] if day else None
        if result and result[-1]["permno"] == permno and result[-1].get("calendar_year") == year:
            result[-1]["rows"] += 1
            if day:
                result[-1]["date_min"] = min(result[-1]["date_min"], day)
                result[-1]["date_max"] = max(result[-1]["date_max"], day)
                result[-1]["date_monotonic"] &= previous_date <= day
        else:
            result.append(
                dict(
                    permno=permno,
                    offset=offset + i,
                    rows=1,
                    row_group=row_group,
                    source=source,
                    date_min=day or date_min,
                    date_max=day or date_max,
                )
            )
            if day:
                result[-1].update(calendar_year=year, date_monotonic=True)
        previous_date = day
    return result


def plan_history(locations, *, start=None, end=None, max_rows=1000):
    if not 1 <= max_rows <= 1000:
        raise ValueError("CRSP_PAGE_BUDGET")
    result = []
    for r in locations:
        if (start and r["date_max"] and r["date_max"] < start) or (
            end and r["date_min"] and r["date_min"] > end
        ):
            continue
        if "offset" not in r:
            result.append(r)
            continue
        for shift in range(0, r["rows"], max_rows):
            result.append(
                dict(r, offset=r["offset"] + shift, rows=min(max_rows, r["rows"] - shift))
            )
    return result


def native_point(row):
    reasons = {}

    def token(name, output):
        value = row.get(name)
        if value is None:
            reasons[output] = "MISSING_NATIVE_VALUE"
            return None
        if isinstance(value, dict) and "non_finite" in value:
            reasons[output] = "NON_FINITE_NATIVE_VALUE"
            return None
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            reasons[output] = "INVALID_NATIVE_VALUE"
            return None
        if not number.is_finite():
            reasons[output] = "NON_FINITE_NATIVE_VALUE"
            return None
        return str(value)

    day = str(row["dlycaldt"])[:10]
    date.fromisoformat(day)
    price = token("dlyprc", "native_price")
    negative = price is not None and Decimal(price) < 0
    result = dict(
        permno=int(row["permno"]),
        session_date=day,
        native_price=price,
        price_magnitude=str(abs(Decimal(price))) if price is not None else None,
        price_observation_type="NEGATIVE_NATIVE_PRICE_BID_ASK_INDICATOR"
        if negative
        else "NATIVE_DLYPRC",
        total_return=token("dlyret", "total_return"),
        price_return=token("dlyretx", "price_return"),
        volume=token("dlyvol", "volume"),
        adjusted_close=None,
        adjustment_method="NOT_DERIVED",
        native_flags={
            k: row.get(k) for k in ("dlyprcflg", "dlyretmissflg", "dlydistretflg", "dlydelflg")
        },
    )
    result["missing_reasons"] = reasons
    return result


def build(entries, locations, sources, *, coverage_sha256):
    shards = defaultdict(dict)
    for permno, entry in entries.items():
        shards[digest(permno.encode())[:2]][permno] = dict(
            entry, fragments=locations.get(permno, [])
        )
    raw_shards = {
        suffix: encode(dict(schema_version=SCHEMA, securities=values))
        for suffix, values in shards.items()
    }
    identity = dict(
        schema_version=SCHEMA,
        provider="WRDS_CRSP",
        rights=RIGHTS,
        source_bucket="financial-data-wrds-private",
        security_count=len(entries),
        indexed_security_count=len(locations),
        indexed_rows=sum(r["rows"] for v in locations.values() for r in v),
        coverage_sha256=coverage_sha256,
        sources=sources,
        price_rows_copied=0,
        price_basis="NATIVE_CRSP_CIZ_DLYPRC",
        adjustment_method="NOT_DERIVED",
        historical_availability="NOT_ESTABLISHED",
        ticker_join="DATED_PERMNO_INTERVALS_ONLY",
        catalog=[
            dict(
                permno=e["permno"],
                security_id=e["security_id"],
                symbol=e["symbol"],
                name=e["name"],
                aliases=sorted({r["symbol"] for r in e["intervals"] if r["symbol"]}),
                date_min=e.get("date_min"),
                date_max=e.get("date_max"),
                normalized_rows=e.get("normalized_rows", 0),
            )
            for e in entries.values()
        ],
        shards={s: dict(sha256=digest(raw), bytes=len(raw)) for s, raw in raw_shards.items()},
    )
    release = digest(encode(identity))
    prefix = PREFIX + release + "/"
    objects = {}
    for suffix, raw in raw_shards.items():
        key = prefix + "shards/" + suffix + ".json"
        objects[key] = raw
        identity["shards"][suffix]["key"] = key
    key = prefix + "manifest.json"
    objects[key] = encode(dict(identity, release_id=release))
    return objects, dict(
        key=key,
        sha256=digest(objects[key]),
        release_id=release,
        security_count=len(entries),
        indexed_rows=identity["indexed_rows"],
    )
