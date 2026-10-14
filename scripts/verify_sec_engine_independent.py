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
import pyarrow as pa

OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale")


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
    verified_total = 0
    duckdb_total = 0
    manifest = json.loads((OUT / "engine-manifest.json").read_text())
    lineage = [
        {"cik": cik, "member_sha256": ref["member_sha256"]}
        for cik, ref in manifest["companies"].items()
    ]
    for entry in lineage:
        terms = []
        all_outputs = {}
        cik = entry["cik"]
        raw = (OUT / (cik + ".companyfacts.json")).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["member_sha256"]
        company = json.loads(raw, parse_float=Decimal)
        records = {}
        latest_contexts = {}
        availability_cache = {}
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
        data = json.loads((OUT / (cik + ".history.json")).read_text())
        assert data["release_id"] == manifest["release_id"]
        assert (
            hashlib.sha256((OUT / (cik + ".history.json")).read_bytes()).hexdigest()
            == manifest["companies"][cik]["sha256"]
        )
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
            assert (
                expected["source_release_id"] == manifest["release_id"]
                and expected["registry_sha256"] == manifest["registry_sha256"]
            )
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
                    cache_key = (tag, unit, cutoff)
                    if cache_key not in latest_contexts:
                        contexts = {}
                        invalid = set()
                        for native in company["facts"]["us-gaap"][tag]["units"][unit]:
                            if native.get("form") not in ("10-K", "10-K/A", "10-Q", "10-Q/A") or not native.get("filed"):
                                continue
                            context = (native.get("start"), native.get("end"))
                            filed = native["filed"]
                            if filed not in availability_cache:
                                try:
                                    availability_cache[filed] = datetime.combine(
                                        date.fromisoformat(filed) + timedelta(days=1),
                                        day_time.min, ZoneInfo("America/New_York")
                                    ).astimezone(timezone.utc)
                                except ValueError:
                                    availability_cache[filed] = None
                            available = availability_cache[filed]
                            if available is None:
                                invalid.add(context)
                            elif available <= cutoff:
                                contexts[context] = max(contexts.get(context, filed), filed)
                        latest_contexts[cache_key] = (contexts, invalid)
                    contexts, invalid = latest_contexts[cache_key]
                    context = (row.get("start"), row["end"])
                    assert context not in invalid, "INVALID_NATIVE_FILING_DATE"
                    latest = contexts[context]
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
        if terms:
            # Vectorized insertion preserves text decimals and integer coefficients.
            # executemany imposed one Python/SQL round trip per source term.
            columns = list(zip(*terms))
            table = pa.table(
                {
                    "result_id": pa.array(columns[0], type=pa.string()),
                    "value_text": pa.array(columns[1], type=pa.string()),
                    "coefficient": pa.array(columns[2], type=pa.int64()),
                }
            )
            con.register("_source_terms", table)
            con.execute("INSERT INTO terms SELECT * FROM _source_terms")
            con.unregister("_source_terms")
        rows = con.execute(
            "SELECT result_id,SUM(CAST(value_text AS DECIMAL(38,9))*coefficient) FROM terms GROUP BY result_id"
        ).fetchall()
        assert len(rows) == len(all_outputs)
        assert all(all_outputs[rid] == value for rid, value in rows)
        verified_total += len(all_outputs)
        duckdb_total += len(rows)
        con.execute("DELETE FROM terms")
    result = {
        "status": "PASS",
        "release_id": manifest["release_id"],
        "registry_sha256": manifest["registry_sha256"],
        "checks": checks,
        "total_observations": verified_total,
        "duckdb_exact_matches": duckdb_total,
        "source_records_normalized": sum(
            len(json.loads((OUT / (e["cik"] + ".history.json")).read_text())["observations"])
            for e in lineage
        ),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "duckdb_threads": 1,
        "duckdb_memory_limit": "64MB",
        "external_access": False,
        "scope": str(len(lineage))
        + "_STORED_PUBLIC_SEC_COMPANYFACTS_MEMBERS;NO_FULL_FINANCIAL_CERTIFICATION",
    }
    (OUT / "independent-reconciliation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}))


if __name__ == "__main__":
    main()
