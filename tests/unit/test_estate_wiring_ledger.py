"""Metadata fixtures prove registration never implies untested row serving."""

import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[2] / "scripts/build_estate_wiring_ledger.py"
    spec = importlib.util.spec_from_file_location("estate_wiring_ledger", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def fixture():
    manifest = "raw/wrds/crsp__msf/releases/one/manifest.json"
    return ({
        "generated_at": "2026-10-05T00:00:00Z",
        "releases": [{
            "dataset": "crsp__msf", "release": "one", "source_library": "crsp",
            "manifest_key": manifest, "manifest_sha256": "a" * 64,
            "objects": [{"key": "raw/wrds/crsp__msf/releases/one/part.parquet"}],
        }],
        "errors": [],
    }, {
        manifest: {"bytes": 100, "etag": "manifest-etag"},
        "raw/wrds/crsp__msf/releases/one/part.parquet": {"bytes": 900, "etag": "p1"},
        "raw/wrds/other__table/releases/two/part.parquet": {"bytes": 500, "etag": "p2"},
        "raw/wrds/other__table/notes.json": {"bytes": 200, "etag": "notes"},
    })


def test_every_namespace_has_exact_registration_and_physical_roles():
    inventory, census = fixture()
    ledger = module().build(inventory, census)
    by_name = {d["dataset_prefix"]: d for d in ledger["datasets"]}
    assert ledger["summary"]["dataset_prefixes"] == 2
    assert ledger["summary"]["objects"] == 4
    assert ledger["summary"]["bytes"] == 1700
    assert by_name["crsp__msf"]["status"] == "native-access"
    assert by_name["crsp__msf"]["role_counts"] == {"source_manifest": 1, "parquet": 1}
    assert by_name["crsp__msf"]["registration"][0]["manifest_sha256"] == "a" * 64
    assert by_name["other__table"]["status"] == "unknown"
    assert by_name["other__table"]["missing_manifest_keys"] == [
        "raw/wrds/other__table/releases/two/manifest.json"
    ]
    assert all(d["semantic_api_status"] == "unserved" for d in ledger["datasets"])
    assert all(d["row_query_verified"] is False for d in ledger["datasets"])


def test_legacy_null_dataset_maps_by_manifest_path_not_declared_name():
    inventory, census = fixture()
    inventory["releases"][0]["dataset"] = None
    ledger = module().build(inventory, census)
    assert ledger["summary"]["registered_named_datasets"] == 0
    assert ledger["summary"]["registered_prefixes"] == 1
    assert ledger["datasets"][0]["registration"][0]["declared_dataset"] is None


def test_order_independent_and_reports_missing_registered_objects():
    inventory, census = fixture()
    record = inventory["releases"][0]
    record["objects"].append({"key": "raw/wrds/crsp__msf/releases/one/missing.parquet"})
    a = module().build(inventory, census)
    b = module().build(inventory, dict(reversed(list(census.items()))))
    assert a == b
    assert a["datasets"][0]["missing_registered_objects"] == [
        "raw/wrds/crsp__msf/releases/one/missing.parquet"
    ]


def test_namespace_exists_even_when_registered_manifest_is_absent():
    inventory, census = fixture()
    del census[inventory["releases"][0]["manifest_key"]]
    ledger = module().build(inventory, census)
    entry = ledger["datasets"][0]
    assert entry["missing_manifest_keys"] == [inventory["releases"][0]["manifest_key"]]
    assert entry["status"] == "unknown"
    assert entry["registration"][0]["catalog_metadata_registered"] is True


@pytest.mark.parametrize("key", [
    "raw/wrds/../releases/one/manifest.json",
    "raw/wrds/a/../../escape.parquet",
    "raw/wrds/a//bad.parquet",
    "other/data.parquet",
])
def test_refuses_keys_outside_safe_census_scope(key):
    inventory, census = fixture()
    census[key] = {"bytes": 1, "etag": "x"}
    with pytest.raises(ValueError):
        module().build(inventory, census)


def test_no_registration_is_inferred_from_unregistered_manifest_presence():
    inventory, census = fixture()
    census["raw/wrds/other__table/releases/two/manifest.json"] = {"bytes": 12, "etag": "x"}
    entry = module().build(inventory, census)["datasets"][1]
    assert entry["registration"] == []
    assert entry["status"] == "unknown"
    assert entry["manifest_discovery_candidates"] == [
        "raw/wrds/other__table/releases/two/manifest.json"
    ]


def test_unresolved_object_reference_does_not_become_native_access_proof():
    inventory, census = fixture()
    inventory["releases"][0]["objects"].append({"key": None})
    entry = module().build(inventory, census)["datasets"][0]
    assert entry["unresolved_registered_object_references"] == 1
    assert entry["status"] == "unknown"
