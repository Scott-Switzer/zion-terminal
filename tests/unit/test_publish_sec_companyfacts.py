"""Companion publication keeps historical approvals pinned and local reuse verified."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "publish_sec_companyfacts", Path(__file__).parents[2] / "scripts/publish_sec_companyfacts.py"
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
RID = "a" * 64
DIGEST = "b" * 64
CIK = "0000320193"


def previous_manifest():
    return {
        "release_id": RID,
        "schema_version": "sec-companyfacts-source-v1",
        "scope": "CONSOLIDATED_PUBLIC_SEC_COMPANY_FACTS",
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "member_count": 1,
        "members": {
            CIK: {
                "key": "gold/sec-companyfacts/releases/" + RID + "/companies/" + CIK + ".json",
                "sha256": "c" * 64,
                "engine_member_sha256": "c" * 64,
                "bytes": 10,
            }
        },
    }


def test_approved_previous_manifest_is_rehashed_and_uses_only_derived_prefix(monkeypatch):
    raw = publisher.encoded(previous_manifest())
    calls = []

    def read(bucket, key, **kwargs):
        calls.append((bucket, key))
        return raw

    monkeypatch.setattr(publisher, "read_object", read)
    digest = publisher.sha(raw)
    approved = publisher.approved_previous([RID + ":" + digest])
    assert approved == {
        RID: {"key": "gold/sec-companyfacts/releases/" + RID + "/manifest.json", "sha256": digest}
    }
    assert calls == [
        ("financial-system-datasets", "gold/sec-companyfacts/releases/" + RID + "/manifest.json")
    ]
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        publisher.approved_previous([RID + ":" + DIGEST])


@pytest.mark.parametrize(
    "reference",
    ["", RID, "../manifest.json:" + DIGEST, RID + ":wrong", RID + ":" + DIGEST + ":extra"],
)
def test_previous_approval_rejects_invalid_references_before_reads(monkeypatch, reference):
    def read(*args, **kwargs):
        pytest.fail("invalid reference must not read storage")

    monkeypatch.setattr(publisher, "read_object", read)
    with pytest.raises(ValueError, match="INVALID_PREVIOUS_RELEASE"):
        publisher.approved_previous([reference])


@pytest.mark.parametrize(
    "change",
    [
        {"release_id": "d" * 64},
        {"rights": "LICENSED"},
        {"licensed_sources_allowed": True},
        {"schema_version": "unknown"},
        {"scope": "unknown"},
        {"member_count": 2},
        {"members": {}},
    ],
)
def test_previous_approval_rejects_invalid_policy_or_member_count(monkeypatch, change):
    value = previous_manifest()
    value.update(change)
    raw = publisher.encoded(value)
    monkeypatch.setattr(publisher, "read_object", lambda *args, **kwargs: raw)
    with pytest.raises(ValueError, match="PREVIOUS_RELEASE_POLICY_OR_MEMBERS_INVALID"):
        publisher.approved_previous([RID + ":" + publisher.sha(raw)])


@pytest.mark.parametrize(
    "change",
    [
        {"bytes": 0},
        {"bytes": 16 * 1048576 + 1},
        {"bytes": True},
        {"key": "gold/sec-companyfacts/releases/other/companies/" + CIK + ".json"},
        {"sha256": "invalid"},
        {"engine_member_sha256": "d" * 64},
    ],
)
def test_previous_approval_rejects_unbounded_or_mixed_members(monkeypatch, change):
    value = previous_manifest()
    value["members"][CIK].update(change)
    raw = publisher.encoded(value)
    monkeypatch.setattr(publisher, "read_object", lambda *args, **kwargs: raw)
    with pytest.raises(ValueError, match="PREVIOUS_RELEASE_POLICY_OR_MEMBERS_INVALID"):
        publisher.approved_previous([RID + ":" + publisher.sha(raw)])


def test_previous_approval_rejects_duplicate_release_pins():
    with pytest.raises(ValueError, match="DUPLICATE_PREVIOUS_RELEASE"):
        publisher.approved_previous([RID + ":" + DIGEST, RID + ":" + "c" * 64])


def reuse_fixture(tmp_path):
    import zlib

    raw = b'{"cik":320193,"entityName":"Test","facts":{}}'
    (tmp_path / (CIK + ".companyfacts.json")).write_bytes(raw)
    entry = {
        "materialized": {"member_sha256": publisher.sha(raw)},
        "archive_entry": {"uncompressed_bytes": len(raw), "crc": zlib.crc32(raw)},
    }
    return raw, {CIK[-2:]: {CIK: entry}}


def test_reuse_extracted_member_checks_engine_pin_crc_and_issuer_without_archive_reads(
    tmp_path, monkeypatch
):
    raw, catalog = reuse_fixture(tmp_path)
    monkeypatch.setattr(
        publisher, "extract_member", lambda *a: pytest.fail("must reuse verified bytes")
    )
    actual, ref = publisher.build_member(
        CIK, {"source": {}}, catalog, verify_local=False, reuse_extracted=tmp_path
    )
    assert actual == raw
    assert ref["sha256"] == publisher.sha(raw)
    assert ref["bytes"] == len(raw)


@pytest.mark.parametrize("change", ["crc", "size", "digest", "issuer"])
def test_reuse_extracted_member_fails_closed_on_tampering(tmp_path, change):
    import zlib

    raw, catalog = reuse_fixture(tmp_path)
    entry = catalog[CIK[-2:]][CIK]
    if change == "crc":
        entry["archive_entry"]["crc"] = 0
    elif change == "size":
        entry["archive_entry"]["uncompressed_bytes"] += 1
    elif change == "digest":
        entry["materialized"]["member_sha256"] = "0" * 64
    else:
        raw = raw.replace(b"320193", b"320194")
        (tmp_path / (CIK + ".companyfacts.json")).write_bytes(raw)
        entry["materialized"]["member_sha256"] = publisher.sha(raw)
        entry["archive_entry"]["crc"] = zlib.crc32(raw)
    with pytest.raises(RuntimeError):
        publisher.build_member(
            CIK, {"source": {}}, catalog, verify_local=False, reuse_extracted=tmp_path
        )


def test_dry_run_cli_preserves_approval_with_reused_members_and_reproducible_manifest(
    tmp_path, monkeypatch
):
    import json

    raw, catalog = reuse_fixture(tmp_path)
    catalog_raw = publisher.encoded(catalog[CIK[-2:]])
    older_raw = publisher.encoded(previous_manifest())
    engine = {
        "release_id": "e" * 64,
        "source": {"snapshot_date": "2026-09-24", "etag": "archive-etag", "key": "archive.zip"},
        "shards": {CIK[-2:]: {"key": "catalog.json", "sha256": publisher.sha(catalog_raw)}},
        "date_from": "2015-01-01",
        "date_to": "2026-09-24",
        "maximum_as_of": "2026-09-24",
    }
    engine_raw = publisher.encoded(engine)
    objects = {
        "engine.json": engine_raw,
        "catalog.json": catalog_raw,
        "gold/sec-companyfacts/releases/" + RID + "/manifest.json": older_raw,
    }
    monkeypatch.setattr(publisher, "read_object", lambda bucket, key, **kw: objects[key])
    monkeypatch.setattr(publisher, "jsonc", lambda path: {"vars": {}})
    monkeypatch.setattr(publisher, "run", lambda *a, **kw: pytest.fail("dry run must never upload"))
    monkeypatch.setattr(
        publisher.sys,
        "argv",
        [
            "publisher",
            "--dry-run",
            "--ciks",
            CIK,
            "--engine-manifest-key",
            "engine.json",
            "--engine-manifest-sha256",
            publisher.sha(engine_raw),
            "--approve-previous",
            RID + ":" + publisher.sha(older_raw),
            "--reuse-extracted-members",
            str(tmp_path),
            "--skip-local-compare",
            "--out",
            str(tmp_path / "candidate"),
        ],
    )
    assert publisher.main() == 0
    manifest_raw = (tmp_path / "candidate/manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest["previous_releases"] == {
        RID: {
            "key": "gold/sec-companyfacts/releases/" + RID + "/manifest.json",
            "sha256": publisher.sha(older_raw),
        }
    }
    assert manifest["member_count"] == 1
    assert (tmp_path / "candidate" / (CIK + ".companyfacts.json")).read_bytes() == raw
    assert publisher.main() == 0
    assert (tmp_path / "candidate/manifest.json").read_bytes() == manifest_raw


def test_distinct_historical_approvals_produce_distinct_immutable_release_identities():
    engine = {"source": {"snapshot_date": "2026-09-24"}, "release_id": "e" * 64}
    members = {
        CIK: {
            "sha256": DIGEST,
            "engine_member_sha256": DIGEST,
            "bytes": 10,
            "archive_entry": {"crc": 1},
        }
    }
    baseline = publisher.index_identity(engine, members)
    first = {RID: {"key": publisher.PREFIX + RID + "/manifest.json", "sha256": DIGEST}}
    second = {RID: {"key": publisher.PREFIX + RID + "/manifest.json", "sha256": "d" * 64}}
    assert publisher.index_identity(engine, members, previous_releases={}) == baseline
    assert publisher.index_identity(engine, members, previous_releases=first) != baseline
    assert publisher.index_identity(
        engine, members, previous_releases=first
    ) != publisher.index_identity(engine, members, previous_releases=second)


def test_archive_reference_mode_changes_immutable_identity_and_emits_no_member_uploads(
    tmp_path, monkeypatch
):
    import json

    raw, catalog = reuse_fixture(tmp_path)
    engine = {
        "release_id": "e" * 64,
        "source": {
            "snapshot_date": "2026-09-24",
            "etag": "archive-etag",
            "key": "sec/bulk/sec-nightly-20260924/companyfacts.zip",
            "bucket": "financial-data-wrds-private",
        },
        "date_from": "2015-01-01",
        "date_to": "2026-09-24",
        "maximum_as_of": "2026-09-24",
    }
    engine_raw = publisher.encoded(engine)
    monkeypatch.setattr(publisher, "jsonc", lambda path: {"vars": {}})
    monkeypatch.setattr(publisher, "read_object", lambda *a, **kw: engine_raw)
    monkeypatch.setattr(publisher, "load_catalog", lambda engine: catalog)
    monkeypatch.setattr(
        publisher.sys,
        "argv",
        [
            "publisher",
            "--dry-run",
            "--ciks",
            CIK,
            "--engine-manifest-key",
            "engine.json",
            "--engine-manifest-sha256",
            publisher.sha(engine_raw),
            "--reuse-extracted-members",
            str(tmp_path),
            "--skip-local-compare",
            "--out",
            str(tmp_path / "candidate"),
            "--archive-references",
        ],
    )
    assert publisher.main() == 0
    manifest = json.loads((tmp_path / "candidate/manifest.json").read_bytes())
    ref = manifest["members"][CIK]
    assert ref["storage"] == "PINNED_SEC_ARCHIVE_MEMBER"
    assert ref["key"] == "sec/bulk/sec-nightly-20260924/companyfacts.zip"
    assert not (tmp_path / "candidate" / (CIK + ".companyfacts.json")).exists()
    baseline = publisher.sha(publisher.index_identity(engine, {CIK: ref}))
    assert manifest["release_id"] != baseline
    uploads = []
    monkeypatch.setattr(publisher, "run", lambda args, **kw: uploads.append(args) or "")
    monkeypatch.setattr(
        publisher,
        "read_object",
        lambda bucket, key, **kw: (
            engine_raw
            if key == "engine.json"
            else (tmp_path / "candidate/manifest.json").read_bytes()
        ),
    )
    monkeypatch.setattr(publisher, "extract_member", lambda source, entry: raw)
    argv = list(publisher.sys.argv)
    argv[argv.index("--dry-run")] = "--publish"
    monkeypatch.setattr(publisher.sys, "argv", argv)
    assert publisher.main() == 0
    assert len(uploads) == 1
    assert uploads[0][-1] == "--immutable"
    assert uploads[0][-2].endswith("/manifest.json")


def test_previous_approval_accepts_guarded_archive_references_for_future_replays(monkeypatch):
    value = previous_manifest()
    value["source"] = {
        "bucket": "financial-data-wrds-private",
        "key": "sec/bulk/sec-nightly-20260924/companyfacts.zip",
    }
    value["members"][CIK].update(
        {
            "storage": "PINNED_SEC_ARCHIVE_MEMBER",
            "key": value["source"]["key"],
            "archive_entry": {
                "name": "CIK" + CIK + ".json",
                "offset": 0,
                "uncompressed_bytes": 10,
                "compressed_bytes": 8,
                "flags": 0,
                "compression": 8,
                "crc": 1,
            },
        }
    )
    raw = publisher.encoded(value)
    monkeypatch.setattr(publisher, "read_object", lambda *a, **kw: raw)
    assert RID in publisher.approved_previous([RID + ":" + publisher.sha(raw)])
    value["source"]["bucket"] = "licensed"
    raw = publisher.encoded(value)
    with pytest.raises(ValueError, match="PREVIOUS_RELEASE_POLICY_OR_MEMBERS_INVALID"):
        publisher.approved_previous([RID + ":" + publisher.sha(raw)])
