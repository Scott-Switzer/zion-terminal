import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "collection_publisher", Path(__file__).parents[2] / "scripts/publish_collection_catalog.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    row = {
        "dataset": "native",
        "release": "r1",
        "source": "WRDS",
        "source_library": "test",
        "columns": [{"name": "arbitrary_native_field", "dtype": "NUMERIC(24,6)"}],
        "objects": [
            {"key": "raw/wrds/native/part.parquet", "sha256": "b" * 64, "bytes": 42, "rows": 3}
        ],
        "manifest_key": "raw/wrds/native/manifest.json",
        "manifest_sha256": "a" * 64,
        "manifest_rows": 3,
    }
    unknown = {
        **row,
        "dataset": None,
        "release": None,
        "manifest_key": "raw/wrds/legacy/manifest.json",
    }
    return {
        "releases": [row, unknown],
        "generated_at": "2026-10-05",
        "errors": [{"error_class": "NoSuchKey"}],
        "table_prefixes": 3,
    }


def test_publisher_preserves_native_fields_unknown_identity_and_declared_provenance():
    objects, metadata = module.build(fixture(), "c" * 64, {"key": "coverage", "sha256": "d" * 64})
    root = json.loads(objects[metadata["key"]])
    assert root["registered_releases"] == 2
    assert root["unique_datasets"] == 1
    assert root["inventory"]["recorded_error_count"] == 1
    assert root["status"] == "PARTIAL"
    assert root["acquisition_enabled"] is False
    entry = next(e for e in root["entries"] if e["dataset"] == "native")
    detail = json.loads(objects[entry["reference"]["key"]])
    assert detail["columns"][0]["name"] == "arbitrary_native_field"
    assert detail["object_hash_status"] == "SOURCE_MANIFEST_DECLARED_NOT_REHASHED"
    assert module.sha(objects[entry["reference"]["key"]]) == entry["reference"]["sha256"]


def test_duplicate_manifest_identity_is_rejected():
    inventory = fixture()
    inventory["releases"].append(inventory["releases"][0])
    with pytest.raises(ValueError, match="duplicate"):
        module.build(inventory, "c" * 64, {})
