"""Prepare/register a bounded isolated full-universe public SEC identity layout.

No stage runs implicitly. prepare uses the existing native R2 client; register uses
only existing Basin operator auth and catalog-vended IO. Never writes CURRENT.
"""

import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from materialize_sec_engine import OUT as BASE

ROOT = BASE.parent / "sec-universe-sprint"
DEFAULT_OUT = ROOT / "basin-prepared"
DATA_CAP = 512_000_000
METADATA_RESERVE = 50_000_000
FRESH_PUBLICATION_RESERVE = 200_000_000
FREE_RESERVE = 600 * 1048576
SQL_CAP = 768_000_000
SQL_REQUESTS = 10
COLUMNS = (
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
    "source_record_json",
    "source_release_id",
)
ISSUER_COLUMNS = ("cik", "label", "member_sha256", "statement_family", "source_release_id")
CASES = [
    (["0000789019"], ["revenue"], "2015-01-01"),
    (["0000019617", "0000104169"], ["net_income", "cash"], "2020-01-01"),
    (["0001018724", "0000070858", "0000002488"], ["operating_cash_flow"], "2010-01-01"),
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def group(cik):
    if not re.fullmatch(r"\d{10}", cik):
        raise ValueError("CIK_FORMAT")
    return zlib.crc32(cik.encode("ascii")) % 16


def disk_guard(path, output_bytes=0, pending_bytes=0):
    if output_bytes + pending_bytes > DATA_CAP:
        raise RuntimeError("STOP_LOCAL_OUTPUT_CAP")
    if shutil.disk_usage(path).free < FREE_RESERVE + pending_bytes:
        raise RuntimeError("STOP_DISK_RESERVE")


def row_digest(rows):
    h = hashlib.sha256()
    count = 0
    for row in rows:
        h.update(canonical(row) + b"\n")
        count += 1
    return h.hexdigest(), count


def normalized(data, cik, release):
    if (
        data.get("rights") != "PUBLIC_SEC_ONLY"
        or data.get("release_id") != release
        or data.get("cik") != cik
    ):
        raise ValueError("SOURCE_PUBLIC_IDENTITY_POLICY")
    rows = []
    for observation in data["observations"]:
        row = {key: observation.get(key) for key in COLUMNS[:13]}
        if row["cik"] != cik or row["member_sha256"] != data["member_sha256"]:
            raise ValueError("SOURCE_ROW_LINEAGE")
        if any(value is not None and not isinstance(value, str) for value in row.values()):
            raise ValueError("SOURCE_STRING_SCHEMA")
        row["source_record_json"] = canonical(observation["source_record"]).decode()
        row["source_release_id"] = release
        row["cik_group"] = group(cik)
        rows.append(row)
    rows.sort(
        key=lambda row: (
            row["metric"] or "",
            row["period_end"] or "",
            row["source_observation_id"] or "",
            canonical(row),
        )
    )
    return rows


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(canonical(value) + b"\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def first_month_ceiling(retained_bytes):
    """Reserve whole GB-month storage units with exact decimal spend arithmetic."""
    if type(retained_bytes) is not int or retained_bytes < 0:
        raise ValueError("RETAINED_BYTES_SCHEMA")
    gb_months = math.ceil(Decimal(retained_bytes) / Decimal(1_000_000_000))
    storage = Decimal(gb_months) * Decimal("0.015")
    dollars = Decimal("4.50") + Decimal("0.36") + storage + Decimal("0.02")
    return gb_months, float(dollars)


def source_inventory(receipt_path, checkpoint_dir, release):
    receipt = json.loads(receipt_path.read_bytes())
    root_raw = (receipt_path.parent / "serving-manifest.json").read_bytes()
    root = json.loads(root_raw)
    if (
        hashlib.sha256(root_raw).hexdigest() != receipt["root"]["sha256"]
        or root["release_id"] != receipt["release_id"]
        or release not in root.get("source_releases", [])
    ):
        raise ValueError("NATIVE_ROOT_PUBLICATION_SOURCE_PIN")
    if root.get("rights") != "PUBLIC_SEC_ONLY" or root.get("licensed_sources_allowed") is not False:
        raise ValueError("NATIVE_ROOT_PUBLIC_POLICY")
    if (
        receipt.get("resource_stop")
        or receipt.get("attempted") != receipt.get("discovered")
        or receipt.get("fully_certified_companies") != 0
    ):
        raise ValueError("SOURCE_RELEASE_NOT_COMPLETE_OR_POLICY")
    records = []
    attempted = 0
    checkpoint_identity = hashlib.sha256(b'{"checkpoints":{')
    for path in sorted(checkpoint_dir.glob("*.json.gz")):
        checkpoint = json.loads(gzip.decompress(path.read_bytes()))
        if checkpoint["context"] != release:
            raise ValueError("CHECKPOINT_RELEASE")
        if attempted:
            checkpoint_identity.update(b",")
        checkpoint_identity.update(canonical(path.name.split(".")[0]) + b":")
        checkpoint_identity.update(
            json.dumps(
                checkpoint,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        )
        attempted += 1
        if checkpoint["status"] != "USABLE":
            continue
        cik, ref = checkpoint["cik"], checkpoint["ref"]
        if path.name != cik + ".json.gz" or checkpoint["verification"]["status"] != "PASS":
            raise ValueError("CHECKPOINT_VERIFICATION")
        if (
            ref["key"] != f"gold/sec-engine/releases/{release}/companies/{cik}.json"
            or not 0 < ref["bytes"] <= 16 * 1048576
        ):
            raise ValueError("CHECKPOINT_SOURCE_SCOPE")
        records.append({"cik": cik, "ref": ref})
    checkpoint_identity.update(b'},"run_id":' + canonical(release) + b"}")
    if checkpoint_identity.hexdigest() != receipt["release_id"]:
        raise ValueError("CHECKPOINT_SET_PUBLICATION_HASH")
    if attempted != receipt["attempted"] or len(records) != receipt["usable"]:
        raise ValueError("CHECKPOINT_RECEIPT_COUNTS")
    if (
        sum(record["ref"]["normalized_revisions"] for record in records)
        != receipt["normalized_revisions"]
    ):
        raise ValueError("CHECKPOINT_RECEIPT_REVISION_COUNTS")
    # Reserve whole paid request units, all retained native bytes, prior experiments,
    # and the maximum new data+metadata before touching the new tables.
    prior = json.loads((BASE / "basin-identity-optimization.json").read_bytes())
    retained = (
        receipt["cloud_bytes_written"]
        + prior["budget"]["persistent"]
        + DATA_CAP
        + METADATA_RESERVE
        + FRESH_PUBLICATION_RESERVE
    )
    storage_gb_months, estimate = first_month_ceiling(retained)
    if estimate > 5 or receipt["cloud_create_calls"] + 1000 > 1_000_000:
        raise RuntimeError("STOP_TASK_SPEND_CAP")
    return records, {
        "native_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "checkpoint_set_publication_identity_verified": True,
        "native_bytes": receipt["cloud_bytes_written"],
        "fresh_publication_retained_reserve_bytes": FRESH_PUBLICATION_RESERVE,
        "prior_basin_budget": prior["budget"],
        "reserved_total_retained_bytes": retained,
        "estimated_first_month_ceiling_usd": estimate,
        "reserved_storage_whole_gb_months": storage_gb_months,
        "storage_rounding": "CEILING_WHOLE_GB_MONTH_EXACT_DECIMAL",
        "source_companies": len(records),
        "normalized_revisions_expected": receipt["normalized_revisions"],
        "source_release_id": release,
        "native_publication_release_id": receipt["release_id"],
        "rights": "PUBLIC_SEC_ONLY",
    }


def aggregate_quality(quality, data):
    """Account already-inspected raw inventory and mapped rejection reasons, no new reads."""

    def add_counts(target, values):
        for reason, count in values.items():
            if not isinstance(reason, str) or type(count) is not int or count < 0:
                raise ValueError("SOURCE_QUALITY_COUNT_SCHEMA")
            target[reason] = target.get(reason, 0) + count

    add_counts(quality["mapped_record_rejections_by_reason"], data.get("rejections", {}))
    inventory = data.get("source_inventory", {}).get("counts")
    if inventory is None:
        quality["companies_missing_source_inventory"] += 1
    else:
        add_counts(quality["source_inventory_counts_INSPECTED"], inventory)
    family = data.get("policy", {}).get("family", "UNSPECIFIED")
    if not isinstance(family, str):
        raise ValueError("SOURCE_POLICY_FAMILY_SCHEMA")
    families = quality["policy_family_company_counts"]
    families[family] = families.get(family, 0) + 1
    quality["companies_inspected"] += 1
    quality["mapped_record_rejections_total"] = sum(
        quality["mapped_record_rejections_by_reason"].values()
    )


def prepare(args):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from reconcile_financial_inventory import client

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    if (out / "prepared.json").exists() or any(out.glob("*.parquet")):
        raise RuntimeError("STOP_EXISTING_PREPARATION")
    records, proof = source_inventory(args.receipt, args.checkpoints, args.release)
    disk_guard(out)
    s = client()
    schema = pa.schema(
        [pa.field(key, pa.string()) for key in COLUMNS] + [pa.field("cik_group", pa.int32())]
    )
    issuer_schema = pa.schema(
        [pa.field(key, pa.string()) for key in ISSUER_COLUMNS] + [pa.field("cik_group", pa.int32())]
    )
    report = {
        "status": "RUNNING",
        "source_policy": proof,
        "groups": [],
        "benchmark_expected": [[] for _ in CASES],
        "companies": [],
        "current_pointer_writes": 0,
        "licensed_rows": 0,
        "fully_certified_companies": 0,
        "record_quality": {
            "qualification": "INSPECTED_NOT_FINANCIALLY_CERTIFIED",
            "source_inventory_counts_INSPECTED": {},
            "mapped_record_rejections_by_reason": {},
            "policy_family_company_counts": {},
            "companies_inspected": 0,
            "companies_missing_source_inventory": 0,
            "rejection_scope": (
                "MAPPED_CONCEPT_SOURCE_ROWS_ONLY;UNMAPPED_RAW_CONCEPTS_NOT_CLASSIFIED"
            ),
            "source_inventory_scope": (
                "ALL_RAW_CONCEPTS_IN_USABLE_COMPANY_PAYLOAD_INVENTORY;QUARANTINED_ISSUERS_EXCLUDED"
            ),
        },
    }
    total_bytes = 0

    def fetch(record):
        disk_guard(out)
        ref = record["ref"]
        response = s.get_object(Bucket="financial-system-datasets", Key=ref["key"])
        try:
            raw = response["Body"].read(ref["bytes"] + 1)
        finally:
            response["Body"].close()
        if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("SOURCE_OBJECT_HASH_SIZE")
        data = json.loads(raw)
        if data["member_sha256"] != ref["member_sha256"]:
            raise ValueError("SOURCE_MEMBER_HASH")
        rows = normalized(data, record["cik"], args.release)
        if len(rows) != ref["normalized_revisions"]:
            raise ValueError("SOURCE_REVISION_COUNT")
        return record, data, rows

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for number in range(16):
                selected = sorted(
                    (record for record in records if group(record["cik"]) == number),
                    key=lambda record: record["cik"],
                )
                if not selected:
                    continue
                path = out / f"group-{number:02d}.parquet"
                writer = pq.ParquetWriter(path, schema, compression="zstd", write_statistics=True)
                pending, buffer = deque(), []
                digest = hashlib.sha256()
                count = 0
                iterator = iter(selected)
                for _ in range(4):
                    record = next(iterator, None)
                    if record is not None:
                        pending.append(pool.submit(fetch, record))

                def flush():
                    nonlocal total_bytes
                    batch = pa.Table.from_pylist(buffer, schema=schema)
                    # Estimate the actual compressed batch in RAM, then reserve 2x plus
                    # footer headroom before writing. Raw Arrow bytes are much larger.
                    estimate = pa.BufferOutputStream()
                    pq.write_table(batch, estimate, compression="zstd", row_group_size=25000)
                    disk_guard(
                        out, total_bytes + path.stat().st_size, estimate.tell() * 2 + 1048576
                    )
                    writer.write_table(batch, row_group_size=25000)
                    buffer.clear()

                try:
                    while pending:
                        record, data, rows = pending.popleft().result()
                        aggregate_quality(report["record_quality"], data)
                        following = next(iterator, None)
                        if following is not None:
                            pending.append(pool.submit(fetch, following))
                        report["companies"].append(
                            {"cik": record["cik"], "rows": len(rows), "group": number}
                        )
                        for row in rows:
                            original = {key: row[key] for key in COLUMNS}
                            digest.update(canonical(original) + b"\n")
                            count += 1
                            for index, (ciks, metrics, lo) in enumerate(CASES):
                                if (
                                    row["cik"] in ciks
                                    and row["metric"] in metrics
                                    and lo <= row["period_end"] <= "2026-09-24"
                                ):
                                    report["benchmark_expected"][index].append(
                                        [row["source_observation_id"], row["value_decimal"]]
                                    )
                            buffer.append(row)
                            if len(buffer) == 25000:
                                flush()
                    if buffer:
                        flush()
                finally:
                    writer.close()
                total_bytes += path.stat().st_size
                disk_guard(out, total_bytes)

                # Sequential local readback independently confirms all original fields and group.
                def local_rows():
                    for batch in pq.ParquetFile(path).iter_batches(batch_size=25000):
                        for row in batch.to_pylist():
                            if row.pop("cik_group") != number or group(row["cik"]) != number:
                                raise ValueError("PARQUET_GROUP_PARITY")
                            yield row

                actual, actual_count = row_digest(local_rows())
                if actual != digest.hexdigest() or actual_count != count:
                    raise ValueError("PARQUET_SOURCE_FULL_PARITY")
                report["groups"].append(
                    {
                        "group": number,
                        "file": path.name,
                        "rows": count,
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "original_rows_sha256": actual,
                        "original_columns_exact_parity": True,
                    }
                )
                save(out / "preparation-progress.json", report)
        issuers = [
            {
                "cik": record["cik"],
                "label": record["ref"]["label"],
                "member_sha256": record["ref"]["member_sha256"],
                "statement_family": record["ref"]["policy"]["family"],
                "source_release_id": args.release,
                "cik_group": group(record["cik"]),
            }
            for record in sorted(records, key=lambda record: record["cik"])
        ]
        path = out / "issuers.parquet"
        arrow = pa.Table.from_pylist(issuers, schema=issuer_schema)
        disk_guard(out, total_bytes, arrow.nbytes * 2 + 1048576)
        pq.write_table(arrow, path, compression="zstd", row_group_size=25000)
        total_bytes += path.stat().st_size
        disk_guard(out, total_bytes)
        report["issuers"] = {
            "file": path.name,
            "rows": len(issuers),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows_sha256": row_digest(issuers)[0],
        }
        for expected in report["benchmark_expected"]:
            expected.sort()
        report.update(
            status="PASS",
            total_data_bytes=total_bytes,
            total_rows=sum(item["rows"] for item in report["groups"]),
            total_data_files=len(report["groups"]) + 1,
        )
        save(out / "prepared.json", report)
    except Exception as exc:
        report.update(status="STOPPED", error=type(exc).__name__ + ": " + str(exc))
        save(out / "preparation-progress.json", report)
        raise


def register(args):
    import pyarrow.parquet as pq
    from publish_basin_public_pilot import ACCOUNT, BUCKET, token
    from pyiceberg.catalog import load_catalog
    from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
    from pyiceberg.io.pyarrow import _pyarrow_to_schema_without_ids
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import assign_fresh_schema_ids
    from pyiceberg.transforms import IdentityTransform

    out = args.output
    prepared = json.loads((out / "prepared.json").read_bytes())
    if (
        prepared["status"] != "PASS"
        or prepared["source_policy"]["source_release_id"] != args.release
    ):
        raise ValueError("PREPARATION_POLICY")
    if (out / "registered.json").exists() or (out / "registration-progress.json").exists():
        raise RuntimeError("STOP_EXISTING_REGISTRATION")
    # Re-read complete native receipt/checkpoints and budget before any catalog writes.
    _, proof = source_inventory(args.receipt, args.checkpoints, args.release)
    if proof["native_receipt_sha256"] != prepared["source_policy"]["native_receipt_sha256"]:
        raise ValueError("SOURCE_RECEIPT_CHANGED")
    namespace = "zion_sec_universe_" + args.release[:12] + "_identity16"
    catalog = load_catalog(
        "r2",
        type="rest",
        uri=f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}",
        warehouse=f"{ACCOUNT}_{BUCKET}",
        token=token(),
    )
    report = {
        "status": "RUNNING",
        "namespace": namespace,
        "source_policy": proof,
        "tables": [],
        "new_data_bytes": 0,
        "measured_metadata_bytes": 0,
        "metadata_reserve_bytes": METADATA_RESERVE,
        "current_pointer_writes": 0,
        "licensed_rows": 0,
        "runtime_read_only_credential_verified": False,
    }
    save(out / "registration-progress.json", report)
    try:
        try:
            catalog.create_namespace(namespace)
        except NamespaceAlreadyExistsError:
            pass
        for name, files in [
            ("canonical_revisions", prepared["groups"]),
            ("issuers", [prepared["issuers"]]),
        ]:
            schema = assign_fresh_schema_ids(
                _pyarrow_to_schema_without_ids(pq.read_schema(out / files[0]["file"]))
            )
            spec = (
                PartitionSpec()
                if name == "issuers"
                else PartitionSpec(
                    PartitionField(
                        source_id=schema.find_field("cik_group").field_id,
                        field_id=1000,
                        transform=IdentityTransform(),
                        name="cik_group_partition",
                    )
                )
            )
            try:
                table = catalog.load_table((namespace, name))
            except NoSuchTableError:
                table = catalog.create_table((namespace, name), schema=schema, partition_spec=spec)
            if table.current_snapshot() is not None:
                raise RuntimeError("STOP_EXISTING_TABLE_SNAPSHOT")
            remote = []
            for item in files:
                path = out / item["file"]
                disk_guard(out, prepared["total_data_bytes"])
                if (
                    path.stat().st_size != item["bytes"]
                    or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
                ):
                    raise ValueError("LOCAL_FILE_HASH_SIZE")
                # Catalog-vended scoped IO; no storage credential copying or expansion.
                location = table.location().rstrip("/") + "/data/" + item["sha256"] + ".parquet"
                with (
                    path.open("rb") as source,
                    table.io.new_output(location).create(overwrite=False) as target,
                ):
                    shutil.copyfileobj(source, target, 1048576)
                remote.append(location)
                report["new_data_bytes"] += item["bytes"]
                if report["new_data_bytes"] > DATA_CAP:
                    raise RuntimeError("STOP_PERSISTENT_CAP")
                # Full remote file bytes and original projection parity, before registration.
                h = hashlib.sha256()
                with table.io.new_input(location).open() as stream:
                    while chunk := stream.read(1048576):
                        h.update(chunk)
                if h.hexdigest() != item["sha256"]:
                    raise ValueError("REMOTE_FILE_BYTE_PARITY")
                with table.io.new_input(location).open() as stream:

                    def rows():
                        for batch in pq.ParquetFile(stream).iter_batches(batch_size=25000):
                            for row in batch.to_pylist():
                                if name == "canonical_revisions":
                                    if (
                                        row.pop("cik_group") != item["group"]
                                        or group(row["cik"]) != item["group"]
                                    ):
                                        raise ValueError("REMOTE_GROUP_PARITY")
                                yield row

                    actual, count = row_digest(rows())
                expected_hash = (
                    item["original_rows_sha256"]
                    if name == "canonical_revisions"
                    else item["rows_sha256"]
                )
                if actual != expected_hash or count != item["rows"]:
                    raise ValueError("REMOTE_SOURCE_ROW_PARITY")
                save(out / "registration-progress.json", report)
            # Documented order-preserving transform, footer stats, automatic name mapping.
            table.add_files(
                remote,
                snapshot_properties={
                    "rights": "PUBLIC_SEC_ONLY",
                    "promotion": "FALSE",
                    "source_release_id": args.release,
                    "preparation_sha256": hashlib.sha256(
                        (out / "prepared.json").read_bytes()
                    ).hexdigest(),
                },
                check_duplicate_files=True,
            )
            table.refresh()
            summary = table.current_snapshot().summary.model_dump()
            if int(summary["total-records"]) != sum(item["rows"] for item in files) or int(
                summary["total-data-files"]
            ) != len(files):
                raise ValueError("CATALOG_COUNTS_PARITY")
            snapshot = table.current_snapshot()
            metadata_locations = {table.metadata_location} | {
                entry.metadata_file for entry in table.metadata.metadata_log
            }
            metadata_bytes = sum(
                len(table.io.new_input(location)) for location in metadata_locations
            )
            metadata_bytes += len(table.io.new_input(snapshot.manifest_list))
            metadata_bytes += sum(
                manifest.manifest_length for manifest in snapshot.manifests(table.io)
            )
            report["measured_metadata_bytes"] += metadata_bytes
            if report["measured_metadata_bytes"] > METADATA_RESERVE:
                raise RuntimeError("STOP_METADATA_RESERVE")
            tasks = list(table.scan().plan_files())
            if sorted(task.file.file_path for task in tasks) != sorted(remote):
                raise ValueError("CATALOG_FILES_PARITY")
            report["tables"].append(
                {
                    "table": namespace + "." + name,
                    "snapshot_id": str(table.current_snapshot().snapshot_id),
                    "rows": int(summary["total-records"]),
                    "files": int(summary["total-data-files"]),
                    "bytes": int(summary["total-files-size"]),
                    "exact_source_projection_parity": True,
                    "metadata_location": table.metadata_location,
                }
            )
        report.update(status="PASS", sql_requests=0, sql_scan_bytes=0)
        save(out / "registered.json", report)
    except Exception as exc:
        report.update(status="STOPPED", error=type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        save(out / "registration-progress.json", report)


class QueryBudget:
    def __init__(self):
        self.requests = 0
        self.bytes = 0

    def reserve(self, worst):
        if self.requests >= SQL_REQUESTS or self.bytes + worst > SQL_CAP:
            raise RuntimeError("STOP_EXPANDED_SQL_CAP")
        self.requests += 1

    def record(self, result):
        metrics = result.get("metrics", {})
        if "bytes_scanned" not in metrics:
            raise RuntimeError("STOP_UNKNOWN_SCAN")
        self.bytes += int(metrics["bytes_scanned"])
        if self.bytes > SQL_CAP:
            raise RuntimeError("STOP_EXPANDED_SCAN_CAP")


def plan_sources(plan):
    found = []
    if plan.get("name") in ("DataSourceExec", "DistributedLeafExec"):
        if not isinstance(plan.get("bytes"), int) or not isinstance(plan.get("files"), int):
            raise ValueError("PLAN_SOURCE_ESTIMATE_MISSING")
        found.append({key: plan[key] for key in ("table", "files", "bytes")})
    for child in plan.get("children", []):
        found.extend(plan_sources(child))
    return found


def scan_reservation(plan, sources):
    """One full pass per source, doubled only for actual late-materialization plans."""

    def has_late(node):
        return node.get("name") == "LateMaterializationExec" or any(
            has_late(child) for child in node.get("children", [])
        )

    passes = 2 if has_late(plan) else 1
    return passes * sum(source["bytes"] for source in sources) + 5_000_000


def full_join_halves(companies, namespace):
    """Disjoint full-universe coverage, each strictly below Basin's 10k row limit."""
    ciks = [item["cik"] for item in companies]
    if len(ciks) != len(set(ciks)):
        raise ValueError("DUPLICATE_COMPANY_IDENTITY")
    queries = []
    covered = []
    for half in range(2):
        groups = list(range(half * 8, (half + 1) * 8))
        expected = [
            [item["cik"], item["rows"]]
            for item in sorted(companies, key=lambda item: item["cik"])
            if group(item["cik"]) in groups
        ]
        if len(expected) >= 10000:
            raise ValueError("FULL_JOIN_HALF_RESPONSE_LIMIT")
        covered.extend(row[0] for row in expected)
        group_sql = ",".join(map(str, groups))
        query = (
            f"SELECT f.cik,COUNT(*) AS n FROM {namespace}.canonical_revisions f "
            f"JOIN {namespace}.issuers i ON f.cik=i.cik "
            f"WHERE f.cik_group IN ({group_sql}) AND i.cik_group IN ({group_sql}) "
            f"GROUP BY f.cik ORDER BY f.cik LIMIT {len(expected) + 1}"
        )
        queries.append(("full_universe_issuer_join_" + str(half), query, expected, groups))
    if sorted(covered) != sorted(ciks):
        raise ValueError("FULL_JOIN_COVERAGE")
    return queries


def validate(args):
    from publish_basin_public_pilot import sql, token

    out = args.output
    prepared = json.loads((out / "prepared.json").read_bytes())
    registered = json.loads((out / "registered.json").read_bytes())
    target = out / "sql-validation.json"
    if target.exists():
        raise RuntimeError("STOP_EXISTING_SQL_EVIDENCE")
    if (
        registered["status"] != "PASS"
        or registered["source_policy"]["source_release_id"] != args.release
    ):
        raise ValueError("REGISTERED_SOURCE_POLICY")
    namespace = registered["namespace"]
    budget = QueryBudget()
    prior = prepared["source_policy"]["prior_basin_budget"]
    report = {
        "status": "RUNNING",
        "queries": [],
        "prior_experiment_budget": prior,
        "new_sql_max_requests": SQL_REQUESTS,
        "new_sql_max_scan_bytes": SQL_CAP,
        "current_pointer_writes": 0,
        "licensed_rows": 0,
    }

    def save_report():
        report["new_sql_requests"] = budget.requests
        report["new_sql_scan_bytes"] = budget.bytes
        report["all_basin_sql_requests"] = prior["requests"] + budget.requests
        report["all_basin_sql_scan_bytes"] = prior["scan"] + budget.bytes
        save(target, report)

    save_report()
    credential = token()
    queries = []
    for index, (ciks, metrics, lo) in enumerate(CASES[:2]):
        if not all(cik in {item["cik"] for item in prepared["companies"]} for cik in ciks):
            continue
        groups = sorted(set(group(cik) for cik in ciks))
        predicate = (
            "cik_group IN ("
            + ",".join(map(str, groups))
            + ") AND cik IN ("
            + ",".join(repr(cik) for cik in ciks)
            + ") AND metric IN ("
            + ",".join(repr(metric) for metric in metrics)
            + f") AND period_end >= '{lo}' AND period_end <= '2026-09-24'"
        )
        query = (
            f"SELECT source_observation_id,value_decimal FROM {namespace}.canonical_revisions "
            f"WHERE {predicate} ORDER BY source_observation_id LIMIT 5001"
        )
        queries.append(("case_" + str(index), query, prepared["benchmark_expected"][index], groups))
    # Prioritize a bounded cross-table check before optional additional cases.
    cik = next(
        (item["cik"] for item in prepared["companies"] if item["cik"] == "0000789019"),
        prepared["companies"][0]["cik"],
    )
    expected_count = next(item["rows"] for item in prepared["companies"] if item["cik"] == cik)
    join = (
        f"SELECT f.cik,COUNT(*) AS n FROM {namespace}.canonical_revisions f "
        f"JOIN {namespace}.issuers i ON f.cik=i.cik "
        f"WHERE f.cik_group={group(cik)} AND f.cik='{cik}' AND i.cik='{cik}' GROUP BY f.cik"
    )
    queries.insert(1, ("bounded_issuer_join", join, [[cik, expected_count]], [group(cik)]))
    queries = full_join_halves(prepared["companies"], namespace) + queries
    try:
        for name, query, expected, groups in queries:
            # EXPLAIN has historically scanned zero bytes; reserve 5MB metadata headroom.
            try:
                budget.reserve(5_000_000)
            except RuntimeError:
                report["queries"].append({"name": name, "status": "SKIPPED_SQL_CAP"})
                continue
            save_report()
            explained, ms = sql("EXPLAIN FORMAT JSON " + query, credential)
            budget.record(explained)
            plan = json.loads(explained["rows"][0]["plan"])
            sources = plan_sources(plan)
            entry = {
                "name": name,
                "query": query,
                "explain": explained,
                "plan_sources": sources,
                "status": "PLANNED",
            }
            report["queries"].append(entry)
            if not sources:
                raise ValueError("PLAN_NO_SOURCE_ESTIMATE")
            facts = [
                source for source in sources if source["table"].endswith(".canonical_revisions")
            ]
            if not facts or any(source["files"] > len(groups) for source in facts):
                raise ValueError("IDENTITY_PRUNING_NOT_PROVEN")
            worst = scan_reservation(plan, sources)
            entry["reserved_scan_bytes"] = worst
            try:
                budget.reserve(worst)
            except RuntimeError:
                entry["status"] = "SKIPPED_ESTIMATED_SCAN_CAP"
                save_report()
                continue
            save_report()
            result, ms = sql(query, credential)
            budget.record(result)
            actual = (
                [[row["cik"], int(row["n"])] for row in result["rows"]]
                if name == "bounded_issuer_join" or name.startswith("full_universe_issuer_join_")
                else [
                    [row["source_observation_id"], row["value_decimal"]] for row in result["rows"]
                ]
            )
            if actual != expected:
                raise ValueError("SQL_EXACT_PARITY")
            entry.update(
                status="PASS",
                rows=len(actual),
                latency_ms=ms,
                metrics=result["metrics"],
                exact_parity=True,
            )
            save_report()
        successes = [entry for entry in report["queries"] if entry["status"] == "PASS"]
        report["status"] = "PASS" if len(successes) == len(queries) else "PARTIAL_BUDGET_LIMIT"
        report["full_universe_join_verified"] = all(
            any(entry["name"] == "full_universe_issuer_join_" + str(half) for entry in successes)
            for half in range(2)
        )
        report["bounded_join_verified"] = any(
            entry["name"] == "bounded_issuer_join" for entry in successes
        )
    except Exception as exc:
        report.update(status="STOPPED", error=type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        save_report()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "register", "validate"])
    parser.add_argument("--release", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=ROOT / "publication.json")
    parser.add_argument("--checkpoints", type=Path, default=ROOT / "checkpoints")
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{64}", args.release):
        raise ValueError("RELEASE_ID")
    {"prepare": prepare, "register": register, "validate": validate}[args.stage](args)


if __name__ == "__main__":
    main()
