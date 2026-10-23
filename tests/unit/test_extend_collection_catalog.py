import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "extend_catalog", Path(__file__).parents[2] / "scripts/extend_collection_catalog.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def manifest():
    base = "raw/wrds/test__table/releases/run-1/"
    return {
        "schema_version": "wrds-remote-text-parquet-v1",
        "source": "WRDS",
        "dataset_id": "test__table",
        "library": "test",
        "table": "table",
        "run_id": "run-1",
        "publication_state": "STAGED",
        "canonical_ready": False,
        "PIT_status": "NOT_ESTABLISHED",
        "availability_time": None,
        "encoding": "POSTGRESQL_NATIVE_TEXT",
        "null_semantics": "SQL_NULL_PRESERVED_AS_PARQUET_NULL",
        "row_count": 2,
        "columns": [
            {"name": "amount", "source_postgresql_type": "numeric(30,9)", "storage_type": "string"}
        ],
        "parts": [
            {"key": base + "part-000000.parquet", "sha256": "b" * 64, "bytes": 20, "rows": 2}
        ],
    }


def test_completed_manifest_enters_existing_catalog_without_numeric_or_pit_claims():
    value = manifest()
    row = module.remote_record(value, "raw/wrds/test__table/releases/run-1/manifest.json", "c" * 64)
    assert row["columns"][0]["dtype"] == "string"
    assert row["columns"][0]["source_postgresql_type"] == "numeric(30,9)"
    assert row["objects"] == value["parts"]
    assert row["normalization"]["status"] == "SOURCE_TEXT_PRESERVED"
    assert row["normalization"]["canonical_ready"] is False
    assert row["manifest_rows"] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"row_count": 3},
        {"publication_state": "RUNNING"},
        {"canonical_ready": True},
        {
            "parts": [
                {"key": "raw/wrds/other/part.parquet", "sha256": "b" * 64, "bytes": 20, "rows": 2}
            ]
        },
        {"availability_time": "2026-01-01"},
        {"columns": []},
    ],
)
def test_partial_inconsistent_or_escaping_manifests_are_rejected(change):
    with pytest.raises(ValueError):
        module.remote_record(
            {**manifest(), **change}, "raw/wrds/test__table/releases/run-1/manifest.json", "c" * 64
        )
