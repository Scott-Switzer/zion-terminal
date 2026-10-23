import importlib.util
import io
import json
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


def test_catalog_merge_preserves_existing_is_idempotent_and_ignores_unmarked_parts():
    old = manifest()
    old["dataset_id"] = "test__old"
    old["parts"] = [
        {**old["parts"][0], "key": "raw/wrds/test__old/releases/run-1/part-000000.parquet"}
    ]
    old_key = "raw/wrds/test__old/releases/run-1/manifest.json"
    encode, sha = module.publisher.encode, module.publisher.sha
    old_raw = encode(old)
    old_record = module.remote_record(old, old_key, sha(old_raw))
    objects, base = module.publisher.build(
        {"releases": [old_record], "generated_at": "2026-10-22", "errors": []}, "a" * 64, {}
    )
    store = {(module.FINANCIAL, k): v for k, v in objects.items()}
    new_key = "raw/wrds/test__table/releases/run-1/manifest.json"
    new_raw = encode(manifest())
    marker = module.STAGED + "run-1/test__table.json"
    store[module.PRIVATE, new_key] = new_raw
    store[module.PRIVATE, marker] = encode({"key": new_key, "sha256": sha(new_raw)})
    store[module.PRIVATE, "raw/wrds/partial/part-000000.parquet"] = b"must not read"
    reads = []

    class FakeS3:
        def get_object(self, Bucket, Key):
            reads.append(Key)
            raw = store[Bucket, Key]
            return {"Body": io.BytesIO(raw), "ContentLength": len(raw)}

        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return self

        def paginate(self, Bucket, Prefix):
            return [
                {"Contents": [{"Key": k} for b, k in store if b == Bucket and k.startswith(Prefix)]}
            ]

    first, metadata = module.extend(FakeS3(), base["key"], base["sha256"])
    second, again = module.extend(FakeS3(), base["key"], base["sha256"])
    assert first == second
    assert metadata["release_id"] == again["release_id"]
    assert metadata["added_releases"] == 1
    root = json.loads(first[metadata["key"]])
    assert {x["dataset"] for x in root["entries"]} == {"test__old", "test__table"}
    prior = next(x for x in root["entries"] if x["dataset"] == "test__old")
    assert json.loads(first[prior["reference"]["key"]])["manifest"]["sha256"] == sha(old_raw)
    assert not any(k.endswith(".parquet") for k in reads)
    store[module.PRIVATE, marker] = encode({"key": new_key, "sha256": "f" * 64})
    with pytest.raises(ValueError, match="METADATA_HASH_MISMATCH"):
        module.extend(FakeS3(), base["key"], base["sha256"])
    changed = {**old, "row_count": 0, "parts": []}
    store[module.PRIVATE, old_key] = encode(changed)
    store[module.PRIVATE, marker] = encode({"key": old_key, "sha256": sha(encode(changed))})
    with pytest.raises(ValueError, match="IMMUTABLE_MANIFEST_CHANGED"):
        module.extend(FakeS3(), base["key"], base["sha256"])
