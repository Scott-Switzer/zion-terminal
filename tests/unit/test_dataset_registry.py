"""Registry tests: bounded discovery, release validation, fallback and integrity.

All cases run against the synthetic fixture tree, so no production storage or
private record is touched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zion_terminal.datasets.cli import load_fixture_bridge
from zion_terminal.datasets.registry import (
    QUALITY_ABSENT,
    QUALITY_SCHEMA_VALIDATED,
    QUALITY_SOURCE_UNVALIDATED,
    FinancialDatasetRegistry,
)
from zion_terminal.datasets.transport import FixtureBridge, ListingPage, ObjectMeta, TransportError

FIXTURE_TREE = Path(__file__).resolve().parents[1] / "fixtures" / "datasets" / "tree"
SERVING_RELEASE = "1" * 32
CORPUS = "sec-filing-intelligence/corpus/v1/" + "a" * 64


@pytest.fixture
def fixture_bridge() -> FixtureBridge:
    return load_fixture_bridge(FIXTURE_TREE)


@pytest.fixture
def registry(fixture_bridge: FixtureBridge) -> FinancialDatasetRegistry:
    return FinancialDatasetRegistry(fixture_bridge)


def test_catalog_declares_families_without_claiming_validation(registry):
    catalog = registry.catalog()
    ids = {family["family_id"] for family in catalog["families"]}
    assert {
        "serving_release_candidate",
        "sec_facts_release",
        "sec_companyfacts_corpus",
        "sec_bulk_archive",
    } <= ids
    assert "not evidence" in catalog["warning"]
    # A pointer probe is recorded as an object read, never as a validated release.
    assert catalog["pointer_probe"]["quality"] == "HASH_VERIFIED"
    assert catalog["pointer_value"]["serving_release_id"] == SERVING_RELEASE


def test_discovery_is_bounded_and_never_claims_completeness_when_truncated(fixture_bridge):
    fixture_bridge.script_listing(
        "research",
        "sec/bulk/",
        ListingPage("research", "sec/bulk/", (), ("sec/bulk/sec-nightly-20260101/",), True, "next"),
    )
    registry = FinancialDatasetRegistry(fixture_bridge)
    discovery = registry.discover("sec_bulk_archive", prefix="sec/bulk/", max_pages=1)
    assert discovery.quality == "PARTIAL"
    assert discovery.complete is False
    assert "LISTING_BOUNDED_BEFORE_COMPLETION" in discovery.findings


def test_blocked_store_is_reported_not_swallowed(fixture_bridge):
    class BrokenStore(FixtureBridge):
        def list_objects(self, *args, **kwargs):
            raise TransportError("STORAGE_UNAVAILABLE", "store unavailable")

    registry = FinancialDatasetRegistry(BrokenStore(objects=fixture_bridge.objects))
    discovery = registry.discover("sec_companyfacts_corpus")
    assert discovery.quality == "BLOCKED"
    assert discovery.findings == ["STORAGE_UNAVAILABLE: store unavailable"]


def test_resolve_release_pins_the_manifest_hash_and_identity(registry):
    release = registry.resolve_release("serving_release_candidate")
    assert release.release_id == SERVING_RELEASE
    assert release.schema_version == "financial-serving-v2"
    assert "MANIFEST_SHA256_MATCH" in release.findings
    assert "MANIFEST_IDENTITY_OK" in release.findings
    assert release.manifest_prefix == f"gold/serving/releases/{SERVING_RELEASE}/"
    assert release.artifact("catalog/metrics.json") is not None
    assert release.released_object_path("catalog/metrics.json").endswith("catalog/metrics.json")


def test_manifest_hash_mismatch_fails_closed(fixture_bridge):
    pointer = json.loads(
        fixture_bridge.objects[("financial", "gold/serving/coverage25/CURRENT.json")].payload
    )
    pointer["manifest_sha256"] = "f" * 64
    fixture_bridge.put_json("financial", "gold/serving/coverage25/CURRENT.json", pointer)
    registry = FinancialDatasetRegistry(fixture_bridge)
    with pytest.raises(TransportError) as error:
        registry.resolve_release("serving_release_candidate")
    assert error.value.code == "MANIFEST_HASH_MISMATCH"


def test_manifest_key_outside_the_release_namespace_is_rejected(fixture_bridge):
    pointer = json.loads(
        fixture_bridge.objects[("financial", "gold/serving/coverage25/CURRENT.json")].payload
    )
    pointer["manifest_key"] = "gold/other/releases/x/manifest.json"
    fixture_bridge.put_json("financial", "gold/serving/coverage25/CURRENT.json", pointer)
    registry = FinancialDatasetRegistry(fixture_bridge)
    with pytest.raises(TransportError) as error:
        registry.resolve_release("serving_release_candidate")
    assert error.value.code == "MANIFEST_KEY_OUTSIDE_FAMILY"


def test_pointer_and_manifest_schema_mismatch_are_distinguished(fixture_bridge):
    pointer = json.loads(
        fixture_bridge.objects[("financial", "gold/serving/coverage25/CURRENT.json")].payload
    )
    pointer["schema_version"] = "financial-serving-v1"
    fixture_bridge.put_json("financial", "gold/serving/coverage25/CURRENT.json", pointer)
    registry = FinancialDatasetRegistry(fixture_bridge)
    with pytest.raises(TransportError) as error:
        registry.resolve_release("serving_release_candidate")
    assert error.value.code == "POINTER_SCHEMA_MISMATCH"


def test_artifact_sampling_detects_a_half_published_release(registry):
    release = registry.resolve_release("serving_release_candidate")
    sample = registry.sample_artifacts(release, limit=3)
    assert sample["complete"] is False
    assert any(path.endswith("catalog/metrics.json") for path in sample["sampled_paths"])
    assert len(sample["missing"]) == 2  # identity and entity artifacts are listed but absent
    assert "MANIFEST_ARTIFACTS_NOT_STORED" in release.findings


def test_sec_facts_release_is_discovered_by_listing_not_hardcoding(registry):
    release = registry.resolve_sec_facts_release()
    assert release is not None
    assert release.manifest.get("tickers") == ["TSTA"]
    assert release.artifact_index["securities/TSTA.json"]["sha256"]


def test_published_security_hash_is_verified_against_the_manifest_pin(registry):
    payload, record, release = registry.published_security("TSTA")
    assert release is not None
    assert payload["cik"] == "0000000001"
    assert record.quality == QUALITY_SCHEMA_VALIDATED
    assert "sha256 matches release manifest pin" in record.notes


def test_published_security_absent_ticker_is_recorded_as_absent(registry):
    payload, record, _ = registry.published_security("TSTB")
    assert payload is None
    assert record.quality == QUALITY_ABSENT


def test_approved_packet_is_pinned_and_schema_checked(registry):
    packet, record = registry.valuation_packet("TSTA")
    assert packet["schema_version"] == "ppe-valuation-packet-v1"
    assert record.quality == QUALITY_SCHEMA_VALIDATED
    assert "sha256 matches approved index pin" in record.notes


def test_tampered_packet_bytes_are_reported_not_trusted(fixture_bridge):
    path = next(path for (store, path) in fixture_bridge.objects if "companies/TSTA.json" in path)
    packet = json.loads(fixture_bridge.objects[("financial", path)].payload)
    packet["bridge"]["cash"]["value"] = 1.0
    fixture_bridge.put_json("financial", path, packet)
    registry = FinancialDatasetRegistry(fixture_bridge)
    _, record = registry.valuation_packet("TSTA")
    assert record.quality == QUALITY_SOURCE_UNVALIDATED
    assert "sha256 differs from approved index pin" in record.notes


def test_malformed_packet_reference_is_rejected(fixture_bridge):
    index = json.loads(
        fixture_bridge.objects[("financial", "control/valuation/CURRENT.json")].payload
    )
    index["companies"]["TSTA"]["key"] = "gold/valuation/releases/short/companies/TSTA.json"
    fixture_bridge.put_json("financial", "control/valuation/CURRENT.json", index)
    registry = FinancialDatasetRegistry(fixture_bridge)
    with pytest.raises(TransportError) as error:
        registry.valuation_packet("TSTA")
    assert error.value.code == "VALUATION_PACKET_REFERENCE_INVALID"


def test_facts_sources_are_ordered_corpus_then_archive_member(registry):
    release = registry.resolve_sec_facts_release()
    candidates = registry.corporate_facts_sources("0000000001", sec_facts_release=release)
    assert [item["kind"] for item in candidates] == ["corpus_companyfacts", "bulk_archive_member"]
    assert candidates[0]["path"].endswith("raw/sec/companyfacts/cik=0000000001/companyfacts.json")
    assert candidates[1]["member"] == "CIK0000000001.json"


def test_archive_member_read_records_crc32_and_ranged_cost(registry):
    payload, record = registry.read_zip_member(
        "research", "sec/bulk/sec-nightly-20260101/companyfacts.zip", "CIK0000000002.json"
    )
    facts = json.loads(payload)
    assert facts["cik"] == 2
    assert record.member == "CIK0000000002.json"
    assert record.member_crc32 and len(record.member_crc32) == 8
    assert record.sha256 == hashlib.sha256(payload).hexdigest()
    assert record.ranged_reads and record.ranged_reads > 0


def test_inventory_records_every_object_read(registry):
    registry.resolve_release("serving_release_candidate")
    registry.read_object("research", "inventory/releases/fixture0000000000/manifest.json")
    inventory = registry.inventory_report()
    paths = {item["path"] for item in inventory["objects"]}
    assert "gold/serving/coverage25/CURRENT.json" in paths
    assert "inventory/releases/fixture0000000000/manifest.json" in paths
    assert all(item["quality"] for item in inventory["objects"])


def test_missing_object_read_is_explicit(registry):
    with pytest.raises(TransportError) as error:
        registry.read_object("financial", "gold/serving/releases/missing/manifest.json")
    assert error.value.code == "OBJECT_NOT_FOUND"


def test_fixture_bridge_listing_object_meta_matches_stored_size(fixture_bridge):
    meta = fixture_bridge.head("financial", "gold/serving/CURRENT.json")
    assert meta.bytes == len(
        fixture_bridge.objects[("financial", "gold/serving/CURRENT.json")].payload
    )
    assert ObjectMeta(store=meta.store, path=meta.path, bytes=meta.bytes, etag=meta.etag) == meta
    assert CORPUS in next(path for (store, path) in fixture_bridge.objects if store == "filings")
