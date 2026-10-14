import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import sec_universe_basin as basin


def sample(cik="0000789019"):
    observation = {key: None for key in basin.COLUMNS[:13]}
    observation.update(
        cik=cik,
        metric="revenue",
        period_end="2025-06-30",
        source_observation_id="s1",
        member_sha256="member",
        value_decimal="9007199254740993.000000001",
        source_record={"val": 123},
    )
    return {
        "rights": "PUBLIC_SEC_ONLY",
        "release_id": "a" * 64,
        "cik": cik,
        "member_sha256": "member",
        "observations": [observation, dict(observation)],
        "rejections": {"UNSUPPORTED_FORM": 3, "INCOMPLETE_SOURCE_CONTEXT": 2},
        "source_inventory": {
            "counts": {"source_records": 100, "candidate_us_periodic_records": 80}
        },
        "policy": {"family": "general_us_gaap_unclassified"},
    }


def test_normalization_exact_precision_duplicates_lineage():
    rows = basin.normalized(sample(), "0000789019", "a" * 64)
    assert len(rows) == 2 and rows[0] == rows[1]
    assert rows[0]["value_decimal"] == "9007199254740993.000000001"
    assert rows[0]["source_record_json"] == '{"val":123}'
    assert rows[0]["cik_group"] == 0
    assert basin.row_digest(rows) != basin.row_digest(rows[:1])
    with pytest.raises(ValueError, match="SOURCE_PUBLIC_IDENTITY_POLICY"):
        basin.normalized({**sample(), "rights": "LICENSED"}, "0000789019", "a" * 64)
    changed = sample()
    changed["observations"][0]["member_sha256"] = "wrong"
    with pytest.raises(ValueError, match="SOURCE_ROW_LINEAGE"):
        basin.normalized(changed, "0000789019", "a" * 64)


def test_group_bounds_and_source_string_schema():
    with pytest.raises(ValueError, match="CIK_FORMAT"):
        basin.group("' OR TRUE")
    value = sample()
    value["observations"][0]["value_decimal"] = 1.25
    with pytest.raises(ValueError, match="SOURCE_STRING_SCHEMA"):
        basin.normalized(value, "0000789019", "a" * 64)


def test_disk_guard_checks_before_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        basin.shutil, "disk_usage", lambda _: SimpleNamespace(free=basin.FREE_RESERVE + 100)
    )
    with pytest.raises(RuntimeError, match="STOP_LOCAL_OUTPUT_CAP"):
        basin.disk_guard(tmp_path, basin.DATA_CAP, 1)
    with pytest.raises(RuntimeError, match="STOP_DISK_RESERVE"):
        basin.disk_guard(tmp_path, 0, 101)


def test_query_budget_fail_closed():
    b = basin.QueryBudget()
    b.requests = basin.SQL_REQUESTS
    with pytest.raises(RuntimeError):
        b.reserve(1)
    assert b.requests == basin.SQL_REQUESTS
    with pytest.raises(RuntimeError, match="STOP_UNKNOWN_SCAN"):
        b.record({})
    b.requests = 0
    b.bytes = basin.SQL_CAP - 10
    with pytest.raises(RuntimeError):
        b.reserve(11)
    assert b.requests == 0


def test_plan_estimates_required_and_nested():
    plan = {
        "name": "LateMaterializationExec",
        "children": [{"name": "DataSourceExec", "files": 1, "bytes": 25, "table": "n.f"}],
    }
    assert basin.plan_sources(plan) == [{"table": "n.f", "files": 1, "bytes": 25}]
    with pytest.raises(ValueError):
        basin.plan_sources({"name": "DataSourceExec", "files": 1})


def test_prepare_compact_parquet_full_parity(monkeypatch, tmp_path):
    pytest.importorskip("pyarrow")
    import reconcile_financial_inventory

    raw = basin.canonical(sample())
    ref = {
        "key": "gold/sec-engine/releases/" + "a" * 64 + "/companies/0000789019.json",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "member_sha256": "member",
        "normalized_revisions": 2,
        "label": "MSFT",
        "policy": {"family": "general"},
    }
    proof = {"source_release_id": "a" * 64, "rights": "PUBLIC_SEC_ONLY"}
    monkeypatch.setattr(
        basin, "source_inventory", lambda *args: ([{"cik": "0000789019", "ref": ref}], proof)
    )
    monkeypatch.setattr(
        reconcile_financial_inventory,
        "client",
        lambda: SimpleNamespace(get_object=lambda **kwargs: {"Body": io.BytesIO(raw)}),
    )
    basin.prepare(
        SimpleNamespace(
            output=tmp_path, receipt=tmp_path / "none", checkpoints=tmp_path, release="a" * 64
        )
    )
    result = json.loads((tmp_path / "prepared.json").read_bytes())
    assert result["status"] == "PASS" and result["total_rows"] == 2
    assert result["total_data_files"] == 2 and result["groups"][0]["original_columns_exact_parity"]
    assert result["benchmark_expected"][0] == [["s1", "9007199254740993.000000001"]] * 2
    quality = result["record_quality"]
    assert quality["source_inventory_counts_INSPECTED"]["source_records"] == 100
    assert quality["mapped_record_rejections_by_reason"] == {
        "UNSUPPORTED_FORM": 3,
        "INCOMPLETE_SOURCE_CONTEXT": 2,
    }
    assert quality["policy_family_company_counts"] == {"general_us_gaap_unclassified": 1}
    assert quality["qualification"] == "INSPECTED_NOT_FINANCIALLY_CERTIFIED"
    with pytest.raises(RuntimeError, match="STOP_EXISTING_PREPARATION"):
        basin.prepare(SimpleNamespace(output=tmp_path))


def test_installed_add_files_identity_footer_inference(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pytest.importorskip("pyiceberg")
    import pyarrow.parquet as pq
    from pyiceberg.io.pyarrow import PyArrowFileIO, parquet_file_to_data_file
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import Schema
    from pyiceberg.table.metadata import new_table_metadata
    from pyiceberg.table.sorting import SortOrder
    from pyiceberg.transforms import IdentityTransform
    from pyiceberg.types import IntegerType, NestedField

    schema = Schema(NestedField(1, "cik_group", IntegerType(), required=False))
    spec = PartitionSpec(
        PartitionField(
            source_id=1, field_id=1000, transform=IdentityTransform(), name="group_partition"
        )
    )
    metadata = new_table_metadata(schema, spec, SortOrder(), str(tmp_path))
    metadata = metadata.model_copy(
        update={
            "properties": {"schema.name-mapping.default": schema.name_mapping.model_dump_json()}
        }
    )
    path = tmp_path / "constant.parquet"
    pq.write_table(
        pa.table({"cik_group": pa.array([3, 3, 3], type=pa.int32())}), path, write_statistics=True
    )
    data_file = parquet_file_to_data_file(PyArrowFileIO(), metadata, str(path))
    assert data_file.record_count == 3 and data_file.partition[0] == 3
    mixed = tmp_path / "mixed.parquet"
    pq.write_table(
        pa.table({"cik_group": pa.array([3, 4], type=pa.int32())}), mixed, write_statistics=True
    )
    with pytest.raises(ValueError, match="more than one partition"):
        parquet_file_to_data_file(PyArrowFileIO(), metadata, str(mixed))


def test_source_gate_separate_publication_identity_and_partial_stop(monkeypatch, tmp_path):
    import gzip

    run = "a" * 64
    publication = "b" * 64
    root = {
        "release_id": publication,
        "source_releases": [run],
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
    }
    root_raw = basin.canonical(root)
    (tmp_path / "serving-manifest.json").write_bytes(root_raw)
    (tmp_path / "basin-identity-optimization.json").write_bytes(
        basin.canonical({"budget": {"persistent": 100179170, "requests": 48, "scan": 508894581}})
    )
    monkeypatch.setattr(basin, "BASE", tmp_path)
    receipt = {
        "release_id": publication,
        "root": {"sha256": hashlib.sha256(root_raw).hexdigest()},
        "resource_stop": None,
        "attempted": 1,
        "discovered": 1,
        "usable": 1,
        "fully_certified_companies": 0,
        "cloud_bytes_written": 7000000000,
        "cloud_create_calls": 30000,
    }
    receipt_path = tmp_path / "publication.json"
    receipt_path.write_bytes(basin.canonical(receipt))
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    checkpoint = {
        "context": run,
        "cik": "0000789019",
        "status": "USABLE",
        "verification": {"status": "PASS"},
        "ref": {"key": f"gold/sec-engine/releases/{run}/companies/0000789019.json", "bytes": 100},
    }
    (checkpoints / "0000789019.json.gz").write_bytes(gzip.compress(basin.canonical(checkpoint)))
    records, proof = basin.source_inventory(receipt_path, checkpoints, run)
    assert len(records) == 1 and proof["native_publication_release_id"] == publication
    assert proof["estimated_first_month_ceiling_usd"] < 5
    receipt["resource_stop"] = "STORAGE_BUDGET"
    receipt_path.write_bytes(basin.canonical(receipt))
    with pytest.raises(ValueError, match="SOURCE_RELEASE_NOT_COMPLETE"):
        basin.source_inventory(receipt_path, checkpoints, run)


def test_expanded_sql_cap_and_actual_plan_passes():
    assert basin.SQL_CAP == 768_000_000
    assert basin.SQL_REQUESTS == 10
    assert basin.DATA_CAP == 512_000_000
    assert basin.FREE_RESERVE == 600 * 1048576
    source = {"name": "DataSourceExec", "table": "n.f", "files": 16, "bytes": 400_000_000}
    estimate = basin.scan_reservation(source, basin.plan_sources(source))
    assert estimate == 405_000_000
    basin.QueryBudget().reserve(estimate)
    late = {"name": "LateMaterializationExec", "children": [source]}
    with pytest.raises(RuntimeError, match="STOP_EXPANDED_SQL_CAP"):
        basin.QueryBudget().reserve(basin.scan_reservation(late, basin.plan_sources(late)))


def test_quality_aggregates_inspected_counts_once_without_certification():
    quality = {
        "mapped_record_rejections_by_reason": {},
        "source_inventory_counts_INSPECTED": {},
        "policy_family_company_counts": {},
        "companies_inspected": 0,
        "companies_missing_source_inventory": 0,
    }
    basin.aggregate_quality(quality, sample())
    basin.aggregate_quality(quality, sample("0000019617"))
    assert quality["mapped_record_rejections_by_reason"] == {
        "UNSUPPORTED_FORM": 6,
        "INCOMPLETE_SOURCE_CONTEXT": 4,
    }
    assert quality["source_inventory_counts_INSPECTED"] == {
        "source_records": 200,
        "candidate_us_periodic_records": 160,
    }
    assert quality["policy_family_company_counts"] == {"general_us_gaap_unclassified": 2}
    assert quality["companies_inspected"] == 2
    with pytest.raises(ValueError, match="SOURCE_QUALITY_COUNT_SCHEMA"):
        basin.aggregate_quality(quality, {"rejections": {"UNKNOWN": -1}})
