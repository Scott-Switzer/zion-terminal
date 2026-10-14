"""Bounded public-only Basin layout experiment; never promotes runtime pointers."""

import hashlib
import json
import math
import urllib.error

from materialize_sec_engine import OUT

MAX_BYTES = 200_000_000
MAX_SCAN = 1_000_000_000
MAX_REQUESTS = 50
REPORT = OUT / "basin-optimization.json"


def digest(rows):
    h = hashlib.sha256()
    for row in sorted(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows):
        h.update(row.encode() + b"\n")
    return h.hexdigest()


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


class Budget:
    def __init__(self):
        self.requests = 0
        self.scan = 0
        self.persistent = 0

    def reserve_query(self, worst_case):
        if self.requests >= MAX_REQUESTS or self.scan + worst_case > MAX_SCAN:
            raise RuntimeError("STOP_SQL_BUDGET")
        self.requests += 1

    def record_scan(self, metrics):
        if "bytes_scanned" not in metrics:
            raise RuntimeError("STOP_UNKNOWN_SCAN")
        self.scan += int(metrics["bytes_scanned"])
        if self.scan > MAX_SCAN:
            raise RuntimeError("STOP_SCAN_CAP")


def main():
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from publish_basin_public_pilot import ACCOUNT, BUCKET, sql, token
    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NoSuchTableError
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.transforms import BucketTransform

    if REPORT.exists():
        raise RuntimeError(
            "STOP_EXISTING_EVIDENCE: preserve cumulative budget; do not rerun automatically"
        )
    credential = token()
    cat = load_catalog(
        "r2",
        type="rest",
        uri=f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}",
        warehouse=f"{ACCOUNT}_{BUCKET}",
        token=credential,
    )
    namespace = "zion_sec_scale_ac8f14de487e"
    baseline = cat.load_table((namespace, "canonical_revisions"))
    assert baseline.current_snapshot().summary.model_dump()["rights"] == "PUBLIC_SEC_ONLY"
    # Read existing public table through vended catalog credentials, without new credentials.
    arrow = baseline.scan().to_arrow()
    expected = digest(arrow.to_pylist())
    arrow = arrow.take(
        pc.sort_indices(
            arrow,
            sort_keys=[
                ("cik", "ascending"),
                ("metric", "ascending"),
                ("period_end", "ascending"),
                ("source_observation_id", "ascending"),
            ],
        )
    )
    budget = Budget()
    report = {
        "status": "RUNNING",
        "namespace": namespace,
        "source_rows": len(arrow),
        "source_multiset_sha256": expected,
        "layouts": [],
        "queries": [],
        "licensed_rows": 0,
        "current_pointer_writes": 0,
        "runtime_read_only_credential_verified": False,
    }

    def save():
        report["budget"] = vars(budget)
        REPORT.write_text(json.dumps(report, indent=2) + "\n")
        REPORT.chmod(0o600)

    layouts = [("canonical_revisions", baseline)]
    try:
        for name, spec in [
            ("canonical_compact_sorted_v1", PartitionSpec()),
            (
                "canonical_bucket16_sorted_v1",
                PartitionSpec(
                    PartitionField(
                        source_id=baseline.schema().find_field("cik").field_id,
                        field_id=1000,
                        transform=BucketTransform(16),
                        name="cik_bucket",
                    )
                ),
            ),
        ]:
            # Compression estimate plus generous metadata headroom, checked BEFORE append.
            sink = pa.BufferOutputStream()
            pq.write_table(arrow, sink, compression="zstd")
            estimate = sink.tell() * 2 + 5_000_000
            if budget.persistent + estimate > MAX_BYTES:
                raise RuntimeError("STOP_PERSISTENT_CAP")
            try:
                table = cat.load_table((namespace, name))
            except NoSuchTableError:
                table = cat.create_table(
                    (namespace, name),
                    schema=baseline.schema(),
                    partition_spec=spec,
                    properties={
                        "write.target-file-size-bytes": "134217728",
                        "write.parquet.row-group-limit": "25000",
                    },
                )
            if table.current_snapshot() is None:
                table.append(
                    arrow,
                    snapshot_properties={
                        "rights": "PUBLIC_SEC_ONLY",
                        "promotion": "FALSE",
                        "source_multiset_sha256": expected,
                    },
                )
            table.refresh()
            summary = table.current_snapshot().summary.model_dump()
            assert summary.get("source_multiset_sha256") == expected
            budget.persistent += int(summary["total-files-size"]) + 5_000_000
            assert budget.persistent <= MAX_BYTES
            actual = digest(table.scan().to_arrow().to_pylist())
            assert actual == expected
            layouts.append((name, table))
            report["layouts"].append(
                {
                    "name": name,
                    "rows": int(summary["total-records"]),
                    "files": int(summary["total-data-files"]),
                    "bytes": int(summary["total-files-size"]),
                    "snapshot_id": str(table.current_snapshot().snapshot_id),
                    "exact_multiset_parity": actual == expected,
                    "partition_spec": str(table.spec()),
                }
            )
            save()
        cases = [
            (["0000789019"], ["revenue"], "2015-01-01"),
            (["0000019617", "0000104169"], ["net_income", "cash"], "2020-01-01"),
            (["0001018724", "0000070858", "0000002488"], ["operating_cash_flow"], "2010-01-01"),
        ]
        rows = arrow.to_pylist()
        for name, table in layouts:
            worst = (
                int(table.current_snapshot().summary.model_dump()["total-files-size"]) + 5_000_000
            )
            for ciks, metrics, lo in cases:
                predicate = (
                    "cik IN ("
                    + ",".join(repr(x) for x in ciks)
                    + ") AND metric IN ("
                    + ",".join(repr(x) for x in metrics)
                    + f") AND period_end >= '{lo}' AND period_end <= '2026-09-24'"
                )
                query = (
                    f"SELECT source_observation_id,value_decimal FROM {namespace}.{name} "
                    f"WHERE {predicate} ORDER BY source_observation_id LIMIT 5001"
                )
                expected_rows = sorted(
                    (r["source_observation_id"], r["value_decimal"])
                    for r in rows
                    if r["cik"] in ciks
                    and r["metric"] in metrics
                    and lo <= r["period_end"] <= "2026-09-24"
                )
                entry = {
                    "table": name,
                    "ciks": ciks,
                    "metrics": metrics,
                    "query": query,
                    "samples": [],
                }
                budget.reserve_query(worst)
                try:
                    plan, ms = sql("EXPLAIN FORMAT JSON " + query, credential)
                    budget.record_scan(plan.get("metrics", {}))
                    entry["explain"] = plan
                except urllib.error.HTTPError as exc:
                    entry["explain_error"] = {
                        "status": exc.code,
                        "body": exc.read(4096).decode(errors="replace"),
                    }
                for repeat in range(3):
                    budget.reserve_query(worst)
                    result, ms = sql(query, credential)
                    budget.record_scan(result["metrics"])
                    assert [
                        (r["source_observation_id"], r["value_decimal"]) for r in result["rows"]
                    ] == expected_rows
                    entry["samples"].append(
                        {
                            "phase": "first_observed" if repeat == 0 else "repeat_observed",
                            "latency_ms": ms,
                            "metrics": result["metrics"],
                            "rows": len(expected_rows),
                            "exact_parity": True,
                        }
                    )
                values = [s["latency_ms"] for s in entry["samples"]]
                entry["p50_ms"] = percentile(values, 0.5)
                entry["p95_ms"] = percentile(values, 0.95)
                entry["first_observed_ms"] = values[0]
                entry["repeat_p50_ms"] = percentile(values[1:], 0.5)
                entry["repeat_p95_ms"] = percentile(values[1:], 0.95)
                report["queries"].append(entry)
                save()
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "STOPPED"
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        save()
    print(json.dumps({"status": report["status"], "report": str(REPORT), "budget": vars(budget)}))


def coarse_group(cik):
    import zlib

    return zlib.crc32(cik.encode("ascii")) % 16


def identity_main():
    """One-shot identity partition candidate, sharing the original run's budgets."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from publish_basin_public_pilot import ACCOUNT, BUCKET, sql, token
    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NoSuchTableError
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import Schema
    from pyiceberg.transforms import IdentityTransform
    from pyiceberg.types import IntegerType, NestedField

    target = OUT / "basin-identity-optimization.json"
    if target.exists():
        raise RuntimeError("STOP_EXISTING_IDENTITY_EVIDENCE")
    prior = json.loads(REPORT.read_text())
    assert prior["status"] == "PASS"
    budget = Budget()
    for key, value in prior["budget"].items():
        setattr(budget, key, value)
    report = {
        "status": "RUNNING",
        "prior_budget": dict(vars(budget)),
        "queries": [],
        "licensed_rows": 0,
        "current_pointer_writes": 0,
        "runtime_read_only_credential_verified": False,
    }

    def save():
        report["budget"] = dict(vars(budget))
        target.write_text(json.dumps(report, indent=2) + "\n")
        target.chmod(0o600)

    save()
    try:
        credential = token()
        cat = load_catalog(
            "r2",
            type="rest",
            uri=f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}",
            warehouse=f"{ACCOUNT}_{BUCKET}",
            token=credential,
        )
        namespace = "zion_sec_scale_ac8f14de487e"
        source = cat.load_table((namespace, "canonical_revisions"))
        assert source.current_snapshot().summary.model_dump()["rights"] == "PUBLIC_SEC_ONLY"
        arrow = source.scan().to_arrow()
        original_columns = arrow.column_names
        expected_hash = digest(arrow.to_pylist())
        assert expected_hash == prior["source_multiset_sha256"]
        arrow = arrow.append_column(
            "cik_group",
            pa.array([coarse_group(x) for x in arrow["cik"].to_pylist()], type=pa.int32()),
        )
        arrow = arrow.take(
            pc.sort_indices(
                arrow,
                sort_keys=[
                    ("cik", "ascending"),
                    ("metric", "ascending"),
                    ("period_end", "ascending"),
                    ("source_observation_id", "ascending"),
                ],
            )
        )
        sink = pa.BufferOutputStream()
        pq.write_table(arrow, sink, compression="zstd")
        if budget.persistent + sink.tell() * 2 + 5_000_000 > MAX_BYTES:
            raise RuntimeError("STOP_PERSISTENT_CAP")
        field_id = max(field.field_id for field in source.schema().fields) + 1
        schema = Schema(
            *source.schema().fields,
            NestedField(field_id, "cik_group", IntegerType(), required=False),
        )
        name = "canonical_identity16_sorted_v1"
        try:
            table = cat.load_table((namespace, name))
        except NoSuchTableError:
            table = cat.create_table(
                (namespace, name),
                schema=schema,
                partition_spec=PartitionSpec(
                    PartitionField(
                        source_id=field_id,
                        field_id=1000,
                        transform=IdentityTransform(),
                        name="cik_group_partition",
                    )
                ),
                properties={
                    "write.target-file-size-bytes": "134217728",
                    "write.parquet.row-group-limit": "25000",
                },
            )
        if table.current_snapshot() is None:
            table.append(
                arrow,
                snapshot_properties={
                    "rights": "PUBLIC_SEC_ONLY",
                    "promotion": "FALSE",
                    "source_multiset_sha256": expected_hash,
                },
            )
        table.refresh()
        summary = table.current_snapshot().summary.model_dump()
        budget.persistent += int(summary["total-files-size"]) + 5_000_000
        save()
        assert budget.persistent <= MAX_BYTES
        returned = table.scan().to_arrow()
        assert digest(returned.select(original_columns).to_pylist()) == expected_hash
        assert all(
            r["cik_group"] == coarse_group(r["cik"])
            for r in returned.select(["cik", "cik_group"]).to_pylist()
        )
        report["layout"] = {
            "table": namespace + "." + name,
            "rows": len(returned),
            "files": int(summary["total-data-files"]),
            "bytes": int(summary["total-files-size"]),
            "snapshot_id": str(table.current_snapshot().snapshot_id),
            "original_columns_exact_multiset_parity": True,
            "deterministic_group_parity": True,
            "source_multiset_sha256": expected_hash,
            "partition_spec": str(table.spec()),
        }
        for baseline in [q for q in prior["queries"] if q["table"] == "canonical_revisions"]:
            ciks = baseline["ciks"]
            groups = sorted(set(coarse_group(cik) for cik in ciks))
            query = (
                baseline["query"]
                .replace(namespace + ".canonical_revisions", namespace + "." + name)
                .replace(" WHERE ", " WHERE cik_group IN (" + ",".join(map(str, groups)) + ") AND ")
            )
            expected = sorted(
                (r["source_observation_id"], r["value_decimal"])
                for r in arrow.to_pylist()
                if r["cik"] in ciks
                and r["metric"] in baseline["metrics"]
                and (baseline["query"].split("period_end >= ")[1].split(" AND ")[0].strip("'"))
                <= r["period_end"]
                <= "2026-09-24"
            )
            entry = {"query": query, "ciks": ciks, "groups": groups, "samples": []}
            report["queries"].append(entry)
            worst = int(summary["total-files-size"]) + 5_000_000
            budget.reserve_query(worst)
            save()
            plan, _ = sql("EXPLAIN FORMAT JSON " + query, credential)
            budget.record_scan(plan.get("metrics", {}))
            entry["explain"] = plan
            save()
            for repeat in range(3):
                budget.reserve_query(worst)
                save()
                result, ms = sql(query, credential)
                budget.record_scan(result["metrics"])
                assert [
                    (r["source_observation_id"], r["value_decimal"]) for r in result["rows"]
                ] == expected
                entry["samples"].append(
                    {
                        "phase": "first_observed" if repeat == 0 else "repeat_observed",
                        "latency_ms": ms,
                        "metrics": result["metrics"],
                        "rows": len(expected),
                        "exact_parity": True,
                    }
                )
                save()
            values = [sample["latency_ms"] for sample in entry["samples"]]
            entry.update(
                p50_ms=percentile(values, 0.5),
                p95_ms=percentile(values, 0.95),
                first_observed_ms=values[0],
                repeat_p50_ms=min(values[1:]),
                repeat_p95_ms=max(values[1:]),
            )
        comparison = []
        baselines = [q for q in prior["queries"] if q["table"] == "canonical_revisions"]
        for entry, baseline in zip(report["queries"], baselines):
            metrics = baseline["samples"][0]["metrics"]
            comparison.append(
                {
                    "ciks": entry["ciks"],
                    "fewer_scanned_files": all(
                        sample["metrics"]["files_scanned"] < metrics["files_scanned"]
                        for sample in entry["samples"]
                    ),
                    "fewer_scan_bytes": all(
                        sample["metrics"]["bytes_scanned"] < metrics["bytes_scanned"]
                        for sample in entry["samples"]
                    ),
                    "lower_p95": entry["p95_ms"] < baseline["p95_ms"],
                    "baseline_p95_ms": baseline["p95_ms"],
                    "identity_p95_ms": entry["p95_ms"],
                }
            )
        report["comparison"] = comparison
        report["strict_all_case_selection_gate"] = all(
            item["fewer_scanned_files"] and item["fewer_scan_bytes"] and item["lower_p95"]
            for item in comparison
        )
        report["selection"] = (
            "CANDIDATE_GATE_PASS"
            if report["strict_all_case_selection_gate"]
            else "RETAIN_ISOLATED_P95_GATE_NOT_MET"
        )
        report["status"] = "PASS"
    except Exception as exc:
        report.update(status="STOPPED", error=type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        save()
    print(json.dumps({"status": report["status"], "report": str(target), "budget": vars(budget)}))


if __name__ == "__main__":
    import sys

    identity_main() if "--identity" in sys.argv else main()
