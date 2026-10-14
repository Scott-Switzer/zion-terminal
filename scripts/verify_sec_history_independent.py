"""Independent raw SEC/Python/DuckDB reconciliation; no implementation calculations imported."""
# ruff: noqa: E501

import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as day_time
from decimal import Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-financial-history")


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def main():
    started = time.perf_counter()
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='64MB'")
    con.execute("SET threads=1")
    con.execute("SET enable_external_access=false")
    con.execute("CREATE TABLE terms (result_id VARCHAR,value_text VARCHAR,coefficient BIGINT)")
    checks = []
    terms = []
    all_outputs = {}
    raw_records = {}
    lineage = json.loads((OUT / "source-lineage.json").read_text())
    for entry in lineage:
        cik = entry["cik"]
        raw = (OUT / (cik + ".companyfacts.json")).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["member_sha256"]
        company = json.loads(raw, parse_float=Decimal)
        records = {}
        for tag, definition in company["facts"]["us-gaap"].items():
            for unit, rows in definition.get("units", {}).items():
                for row in rows:
                    original = {
                        k: format(v, "f") if isinstance(v, Decimal) else v for k, v in row.items()
                    }
                    sid = hashlib.sha256(
                        encoded(
                            {"cik": cik, "concept": tag, "unit": unit, "source_record": original}
                        )
                    ).hexdigest()
                    records[sid] = (tag, unit, row)
        raw_records.update(records)
        data = json.loads((OUT / (cik + ".history.json")).read_text())
        normalized = {r["source_observation_id"]: r for r in data["observations"]}
        for sid, r in normalized.items():
            tag, unit, original = records[sid]
            assert (
                r["concept"] == "us-gaap:" + tag
                and r["unit"] == unit
                and r["period_end"] == original["end"]
                and r["period_start"] == original.get("start")
            )
            assert (Decimal(r["value_decimal"]) if r["value_decimal"] is not None else None) == (
                Decimal(str(original["val"])) if original.get("val") is not None else None
            )
        for frequency in ["annual", "quarterly", "ttm"]:
            expected = json.loads((OUT / (cik + "." + frequency + ".expected.json")).read_text())
            count = 0
            for index, result in enumerate(expected["observations"]):
                rid = cik + ":" + frequency + ":" + str(index)
                actual = Decimal(result["value_decimal"])
                total = Decimal(0)
                with localcontext() as ctx:
                    ctx.prec = 200
                    for term in result["source_terms"]:
                        tag, unit, row = records[term["source_observation_id"]]
                        value = Decimal(str(row["val"]))
                        coefficient = term["coefficient"]
                        assert (
                            value.is_finite()
                            and value.as_tuple().exponent >= -9
                            and value.adjusted() < 29
                        )
                        total += value * coefficient
                        terms.append((rid, format(value, "f"), coefficient))
                assert total == actual, ("independent arithmetic mismatch", rid)
                for sid in result["source_observation_ids"]:
                    tag, unit, row = records[sid]
                    availability = datetime.combine(
                        date.fromisoformat(row["filed"]) + timedelta(days=1),
                        day_time.min,
                        ZoneInfo("America/New_York"),
                    ).astimezone(timezone.utc)
                    cutoff = datetime.fromisoformat(expected["as_of"].replace("Z", "+00:00"))
                    assert availability <= cutoff
                    # Verify that no later eligible record for this exact native context supersedes it.
                    eligible = [
                        x
                        for x in company["facts"]["us-gaap"][tag]["units"][unit]
                        if x.get("form") in ("10-K", "10-K/A", "10-Q", "10-Q/A")
                        and x.get("start") == row.get("start")
                        and x.get("end") == row["end"]
                        and x.get("filed")
                        and datetime.combine(
                            date.fromisoformat(x["filed"]) + timedelta(days=1),
                            day_time.min,
                            ZoneInfo("America/New_York"),
                        ).astimezone(timezone.utc)
                        <= cutoff
                    ]
                    latest = max(x["filed"] for x in eligible)
                    assert row["filed"] == latest
                all_outputs[rid] = actual
                count += 1
            checks.append(
                {
                    "cik": cik,
                    "frequency": frequency,
                    "observations_independently_reconciled": count,
                    "status": "PASS",
                }
            )
    con.executemany("INSERT INTO terms VALUES (?,?,?)", terms)
    rows = con.execute(
        "SELECT result_id,SUM(CAST(value_text AS DECIMAL(38,9))*coefficient) FROM terms GROUP BY result_id"
    ).fetchall()
    assert len(rows) == len(all_outputs)
    assert all(all_outputs[rid] == value for rid, value in rows)
    result = {
        "status": "PASS",
        "checks": checks,
        "total_observations": len(all_outputs),
        "duckdb_exact_matches": len(rows),
        "source_records_normalized": sum(
            len(json.loads((OUT / (e["cik"] + ".history.json")).read_text())["observations"])
            for e in lineage
        ),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "duckdb_threads": 1,
        "duckdb_memory_limit": "64MB",
        "external_access": False,
        "scope": "FIVE_STORED_PUBLIC_SEC_COMPANYFACTS_MEMBERS;NO_LIVE_PROVIDER_REQUEST;NO_FULL_FINANCIAL_CERTIFICATION",
    }
    (OUT / "independent-reconciliation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
