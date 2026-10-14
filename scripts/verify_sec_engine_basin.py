"""Evaluate isolated partitioned public canonical revisions; operator credential only."""

import json
from collections import Counter

import pyarrow as pa
from materialize_sec_engine import OUT
from publish_basin_public_pilot import ACCOUNT, BUCKET, sql, token
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.io.pyarrow import _pyarrow_to_schema_without_ids
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import assign_fresh_schema_ids
from pyiceberg.transforms import BucketTransform, TruncateTransform


def main():
    manifest = json.loads((OUT / "engine-manifest.json").read_text())
    release = manifest["release_id"]
    namespace = "zion_sec_scale_" + release[:12]
    cat = load_catalog(
        "r2",
        type="rest",
        uri=f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}",
        warehouse=f"{ACCOUNT}_{BUCKET}",
        token=token(),
    )
    try:
        cat.create_namespace(namespace)
    except NamespaceAlreadyExistsError:
        pass
    rows = []
    issuers = []
    for cik, ref in manifest["companies"].items():
        data = json.loads((OUT / (cik + ".history.json")).read_text())
        assert data["rights"] == "PUBLIC_SEC_ONLY"
        for r in data["observations"]:
            row = {
                k: r.get(k)
                for k in (
                    "cik",
                    "metric",
                    "concept",
                    "period_start",
                    "period_end",
                    "value_decimal",
                    "unit",
                    "currency",
                    "available_at",
                    "source_observation_id",
                    "member_sha256",
                    "accession",
                    "form",
                )
            }
            row["source_record_json"] = json.dumps(
                r["source_record"], sort_keys=True, separators=(",", ":")
            )
            row["source_release_id"] = release
            rows.append(row)
        issuers.append(
            {
                "cik": cik,
                "label": ref["label"],
                "member_sha256": ref["member_sha256"],
                "statement_family": ref["policy"]["family"],
            }
        )
    pins = []
    for name, records in [("canonical_revisions", rows), ("issuers", issuers)]:
        arrow = pa.table(
            {k: pa.array([r.get(k) for r in records], type=pa.string()) for k in records[0]}
        )
        schema = assign_fresh_schema_ids(_pyarrow_to_schema_without_ids(arrow.schema))
        spec = PartitionSpec(
            PartitionField(
                source_id=schema.find_field("cik").field_id,
                field_id=1000,
                transform=BucketTransform(16),
                name="cik_bucket",
            )
        )
        if name == "canonical_revisions":
            spec = PartitionSpec(
                *spec.fields,
                PartitionField(
                    source_id=schema.find_field("period_end").field_id,
                    field_id=1001,
                    transform=TruncateTransform(4),
                    name="period_year",
                ),
            )
        try:
            table = cat.load_table((namespace, name))
        except NoSuchTableError:
            table = cat.create_table((namespace, name), schema=schema, partition_spec=spec)
        if table.current_snapshot() is None:
            table.append(
                arrow,
                snapshot_properties={
                    "source_release_id": release,
                    "rights": "PUBLIC_SEC_ONLY",
                    "promotion": "FALSE",
                },
            )
        table.refresh()
        snapshot = table.current_snapshot()
        summary = snapshot.summary.model_dump()
        assert summary["source_release_id"] == release and int(summary["total-records"]) == len(
            records
        )
        pins.append(
            {
                "table": namespace + "." + name,
                "snapshot_id": str(snapshot.snapshot_id),
                "metadata_location": table.metadata_location,
                "partition_spec": str(table.spec()),
                "records": len(records),
                "files": int(summary["total-data-files"]),
                "bytes": int(summary["total-files-size"]),
            }
        )
    result, latency = sql(
        (
            f"SELECT f.cik, COUNT(*) AS n FROM {namespace}.canonical_revisions f "
            f"JOIN {namespace}.issuers i ON f.cik=i.cik GROUP BY f.cik ORDER BY f.cik"
        ),
        token(),
    )
    expected = Counter(r["cik"] for r in rows)
    assert {r["cik"]: int(r["n"]) for r in result["rows"]} == dict(expected)
    checks = [
        {
            "name": "all_company_revision_counts_join",
            "status": "PASS",
            "companies": len(expected),
            "revisions": len(rows),
            "latency_ms": latency,
            "metrics": result["metrics"],
        }
    ]
    for ciks, metrics, lo, hi in [
        (["0000789019"], ["revenue"], "2015-01-01", "2026-09-24"),
        (["0000019617", "0000104169"], ["net_income", "cash"], "2020-01-01", "2026-09-24"),
        (
            ["0001018724", "0000070858", "0000002488"],
            ["operating_cash_flow"],
            "2010-01-01",
            "2026-09-24",
        ),
    ]:
        ciksql = ",".join("'" + x + "'" for x in ciks)
        msql = ",".join("'" + x + "'" for x in metrics)
        q = (
            f"SELECT source_observation_id,value_decimal FROM {namespace}.canonical_revisions "
            f"WHERE cik IN ({ciksql}) AND metric IN ({msql}) AND period_end>='{lo}' "
            f"AND period_end<='{hi}' ORDER BY source_observation_id LIMIT 5001"
        )
        r, t = sql(q, token())
        wanted = sorted(
            (x["source_observation_id"], x["value_decimal"])
            for x in rows
            if x["cik"] in ciks and x["metric"] in metrics and lo <= x["period_end"] <= hi
        )
        assert wanted == [(x["source_observation_id"], x["value_decimal"]) for x in r["rows"]]
        checks.append(
            {
                "name": "bounded_partition_query",
                "status": "PASS",
                "ciks": ciks,
                "metrics_selected": metrics,
                "rows": len(wanted),
                "latency_ms": t,
                "metrics": r["metrics"],
            }
        )
    report = {
        "status": "PASS",
        "release_id": release,
        "tables": pins,
        "checks": checks,
        "runtime_read_only_credential_verified": False,
        "use": "OPERATOR_EVALUATION_ONLY;NO_RUNTIME_PRIVILEGE_EXPANSION",
        "licensed_rows": 0,
        "current_pointer_writes": 0,
    }
    (OUT / "basin-partition-evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
