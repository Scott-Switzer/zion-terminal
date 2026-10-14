import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "owner_market", Path(__file__).parents[2] / "scripts/prepare_owner_market_release.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(**changes):
    value = dict(
        provider_symbol="TEST",
        session_date="2020-01-02",
        feed="SIP",
        price_basis="RAW",
        adjustment_method="NONE",
        currency="UNKNOWN",
        open=1.1,
        high=2,
        low=1,
        close=1.5,
        volume=10,
        vwap=None,
        transactions=2,
        source_object_key="raw/alpaca/equity-bars/x.json",
        source_hash="a" * 64,
        retrieved_at="2026-01-01T00:00:00Z",
        market_available_at=None,
        alpaca_asset_uuid="current-only",
    )
    value.update(changes)
    return value


def test_duplicates_keep_every_source_id_without_historical_uuid():
    rows = [row(), row(source_hash="b" * 64)]
    data = m.project("TEST", rows, {})
    assert len(data["observations"]) == 1
    assert len(data["observations"][0]["source_record_ids"]) == 2
    assert data["security_id"] == "alpaca:sip:source-symbol:TEST"
    assert data["historical_identity_status"] == "NOT_ESTABLISHED"
    assert data["observations"][0]["open"] == "1.1"
    assert data["observations"][0]["currency"] == "UNKNOWN"


def test_conflicting_provider_bars_are_gap_not_latest():
    data = m.project("TEST", [row(), row(close=1.6, source_hash="b" * 64)], {})
    assert data["observations"] == []
    assert data["gaps"][0]["reason"] == "CONFLICTING_SOURCE_BARS"
    assert len(data["gaps"][0]["source_record_ids"]) == 2


def test_currency_only_from_verified_request_contract():
    proof = m.currency_contract(
        dict(
            endpoint="https://data.alpaca.markets/v2/stocks/bars?asof=-&adjustment=raw&feed=sip",
            asof="-",
            adjustment="raw",
            feed="sip",
        )
    )
    data = m.project("TEST", [row()], {"a" * 64: proof})
    assert data["observations"][0]["currency"] == "USD"
    assert data["observations"][0]["source_currency"] == "UNKNOWN"
    assert data["observations"][0]["market_available_at"] is None
    assert data["observations"][0]["currency_evidence"]["basis"] == "DOCUMENTED_REQUEST_DEFAULT"
    with pytest.raises(ValueError):
        m.currency_contract(
            dict(
                endpoint="https://evil.invalid/?currency=USD",
                asof="-",
                adjustment="raw",
                feed="sip",
            )
        )


def test_nonfinite_or_wrong_basis_rejected():
    with pytest.raises(ValueError):
        m.project("TEST", [row(close=float("nan"))], {})
    with pytest.raises(ValueError):
        m.project("TEST", [row(adjustment_method="SPLIT")], {})


def test_original_decimal_lexical_precision_retained_and_source_resolvable():
    from decimal import Decimal

    data = m.project("TEST", [row(open=Decimal("1.10000000000000000001"))], {}, raw_verified=True)
    assert data["observations"][0]["open"] == "1.10000000000000000001"
    assert data["numeric_representation"] == "ORIGINAL_RAW_DECIMAL_TOKEN_VERIFIED"
    source_id = data["observations"][0]["source_record_ids"][0]
    record = data["source_records"][source_id]
    assert data["source_pages"][record["source_page_sha256"]]["key"].startswith("raw/alpaca/")
    assert (
        m.project("TEST", [row()], {})["numeric_representation"] == "FLOAT64_PROJECTION_UNCERTIFIED"
    )


def test_duplicate_proof_metadata_does_not_make_financial_conflict():
    proof = {"currency": "USD", "basis": "EXPLICIT_REQUEST_CURRENCY"}
    data = m.project(
        "TEST",
        [row(), row(source_hash="b" * 64)],
        {"a" * 64: dict(proof, marker=1), "b" * 64: dict(proof, marker=2)},
    )
    assert len(data["observations"]) == 1
    assert len(data["observations"][0]["source_record_ids"]) == 2
