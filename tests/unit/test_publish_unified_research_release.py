import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "unified_publish", Path(__file__).parents[2] / "scripts/publish_unified_research_release.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_rights_fail_closed():
    m.public_sec({"rights": "PUBLIC_SEC_ONLY", "licensed_sources_allowed": False})
    with pytest.raises(ValueError):
        m.public_sec({"rights": "WRDS_PRIVATE", "licensed_sources_allowed": False})
    with pytest.raises(ValueError):
        m.public_sec({"rights": "PUBLIC_SEC_ONLY", "licensed_sources_allowed": True})


def test_reference_exact_bytes():
    r = m.ref("gold/unified-research/releases/test/data.json", b"{}")
    assert r["bytes"] == 2
    assert r["sha256"] == m.sha(b"{}")
    with pytest.raises(ValueError):
        m.ref("control/CURRENT.json", b"{}")


class Store:
    def __init__(self):
        self.data = {}
        self.calls = 0

    def put_object(self, **args):
        assert args["IfNoneMatch"] == "*"
        self.calls += 1
        self.data[args["Key"]] = args["Body"]

    def get_object(self, **args):
        raw = self.data[args["Key"]]
        return {"Body": io.BytesIO(raw), "ContentLength": len(raw)}


def test_cloud_create_readback_and_budget():
    s = Store()
    p = m.Publisher(s)
    r = p.write("gold/unified-research/releases/test/one.json", b"{}")
    assert r["sha256"] == m.sha(b"{}") and p.bytes == 2 and p.creates == 1
    p.bytes = 12 * 1024**2
    with pytest.raises(ValueError):
        p.write("gold/unified-research/releases/test/two.json", b"{}")
    assert s.calls == 1


def test_source_hash_mismatch_read_fails():
    s = Store()
    s.data["gold/unified-research/releases/test/x"] = b"changed"
    with pytest.raises(ValueError):
        m.read_verified(
            s,
            {
                "key": "gold/unified-research/releases/test/x",
                "sha256": m.sha(b"original"),
                "bytes": 7,
            },
            100,
        )


def test_owner_policy_cannot_relabel_wrds_or_invent_availability():
    data = dict(
        rights=m.RIGHTS,
        provider_symbol="TEST",
        schema_version="owner-market-history-v1",
        numeric_representation="ORIGINAL_RAW_DECIMAL_TOKEN_VERIFIED",
        historical_cik_bridge=None,
        feed="SIP",
        price_basis="RAW",
        adjustment_method="NONE",
        source_pages={"abc": dict(bucket=m.BUCKET, key="raw/wrds/private.parquet", sha256="abc")},
        source_records={},
        observations=[],
    )
    with pytest.raises(ValueError):
        m.validate_market(data, "TEST")
    data["source_pages"]["abc"]["key"] = "raw/alpaca/equity-bars/page.json"
    m.validate_market(data, "TEST")
    data["observations"] = [
        dict(market_available_at="2020-01-01T00:00:00Z", source_record_ids=["id"])
    ]
    with pytest.raises(ValueError):
        m.validate_market(data, "TEST")


def test_existing_immutable_conflict_is_verified_without_overwrite():
    class Exists(Exception):
        response = {"Error": {"Code": "PreconditionFailed"}}

    class ExistingStore(Store):
        def put_object(self, **args):
            raise Exists()

    s = ExistingStore()
    key = "gold/unified-research/releases/test/existing.json"
    s.data[key] = b"{}"
    p = m.Publisher(s)
    p.write(key, b"{}")
    assert p.bytes == 0 and p.existing_objects == 1 and p.readbacks == 1
    with pytest.raises(ValueError):
        p.write(key, b"[]")
    assert s.data[key] == b"{}"


def test_point_join_requires_literal_identity_price_and_exact_historical_proof():
    identity = {"identity_anchors": [dict(symbol="TEST", observed_on="2019-11-01")]}
    market = {"TEST": {"observations": [dict(session_date="2019-11-01")]}}
    companion = dict(
        member_sha256="member", registry_sha256="registry", historical_reconciliation={}
    )

    def resolver(anchors, symbol, date):
        return {"cik": "0000000001"}

    with pytest.raises(ValueError):
        m.point_join_gate(identity, market, {"0000000001": companion}, resolver, 1)
    proof = dict(
        as_of="2019-11-01T00:00:00Z",
        member_sha256="member",
        registry_sha256="registry",
        checks=[dict(status="PASS")],
    )
    companion["historical_reconciliation"][proof["as_of"]] = proof
    joined, gaps = m.point_join_gate(identity, market, {"0000000001": companion}, resolver, 1)
    assert len(joined) == 1 and not gaps
    proof["as_of"] = "2026-09-24T00:00:00Z"
    with pytest.raises(ValueError):
        m.point_join_gate(identity, market, {"0000000001": companion}, resolver, 1)


def test_ambiguous_identity_cannot_count_toward_join_gate():
    def resolver(*args):
        raise ValueError("AMBIGUOUS_SECURITY_IDENTITY")

    with pytest.raises(ValueError):
        m.point_join_gate(
            {"identity_anchors": [dict(symbol="TEST", observed_on="2019-11-01")]},
            {"TEST": {"observations": []}},
            {},
            resolver,
            1,
        )


def test_index_preserves_exact_source_entry_and_shard_pin():
    cik = "0000000001"
    release = "a" * 64
    ref = dict(
        key="gold/sec-engine/releases/" + release + "/catalog-01.json", sha256="b" * 64, bytes=100
    )
    root = dict(shards={"01": ref}, source_releases=[release])
    source = dict(
        label="Source label",
        archive_entry={"crc": 123},
        policy={"family": "source-family"},
        materialized=dict(
            key="gold/sec-engine/releases/" + release + "/companies/" + cik + ".json",
            sha256="c" * 64,
            member_sha256="d" * 64,
            bytes=1000,
            source_release_id=release,
        ),
    )
    indexed = m.indexed_financial_entry(cik, source, ref, root)
    assert {k: v for k, v in indexed.items() if k != "source_shard_ref"} == source
    assert indexed["source_shard_ref"] == ref and "source_shard_ref" not in source
    with pytest.raises(ValueError):
        m.indexed_financial_entry(cik, source, dict(ref, sha256="e" * 64), root)
    with pytest.raises(ValueError):
        m.indexed_financial_entry(cik, dict(source, source_shard_ref=ref), ref, root)
    with pytest.raises(ValueError):
        m.indexed_financial_entry(
            cik,
            dict(source, materialized=dict(source["materialized"], source_release_id="f" * 64)),
            ref,
            root,
        )
