import hashlib
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/cloudflare-query/src"))
m = importlib.import_module("security_identity")


def fixture(shares="1,234", unit="shares", scale="3"):
    return f'''<html>
<xbrli:context id="cover">
<xbrli:entity>
<xbrli:identifier scheme="http://www.sec.gov/CIK">320193</xbrli:identifier>
</xbrli:entity>
<xbrli:period>
<xbrli:instant>2019-09-28</xbrli:instant>
</xbrli:period>
</xbrli:context>
<xbrli:context id="shares">
<xbrli:entity>
<xbrli:identifier scheme="http://www.sec.gov/CIK">320193</xbrli:identifier>
<xbrli:segment>
<xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">
aapl:CommonStockMember
</xbrldi:explicitMember>
</xbrli:segment>
</xbrli:entity>
<xbrli:period>
<xbrli:instant>2019-10-18</xbrli:instant>
</xbrli:period>
</xbrli:context>
<xbrli:unit id="u">
<xbrli:measure>{unit}</xbrli:measure>
</xbrli:unit>
<ix:nonNumeric name="dei:DocumentType" contextRef="cover">10-K</ix:nonNumeric>
<ix:nonNumeric name="dei:TradingSymbol" contextRef="cover">AAPL</ix:nonNumeric>
<ix:nonNumeric name="dei:SecurityExchangeName" contextRef="cover">NASDAQ</ix:nonNumeric>
<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="shares"
unitRef="u" scale="{scale}">{shares}</ix:nonFraction>
<ix:nonFraction name="us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"
contextRef="shares" unitRef="u">999999</ix:nonFraction>
</html>'''.encode()


def parse(raw=None, **overrides):
    raw = fixture() if raw is None else raw
    args = dict(
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        cik="0000320193",
        accession="0000320193-19-000119",
        filing_date="2019-10-31",
        source_key="public-test-fixture",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019319000119/main.htm",
        metadata_sha256="a" * 64,
    )
    args.update(overrides)
    return m.filing_anchors(raw, **args)


def test_source_dates_units_dimensions_are_distinct_and_no_weighted_average():
    result = parse()
    anchor = result["identity_anchors"][0]
    assert anchor["observed_on"] == "2019-10-31"
    assert anchor["context_date"] == "2019-09-28"
    assert anchor["valid_from"] == anchor["valid_to"]
    shares = result["outstanding_shares"]
    assert len(shares) == 1
    assert shares[0]["value_decimal"] == "1234000"
    assert shares[0]["shares_date"] == "2019-10-18"
    assert shares[0]["share_scope"] == "CLASS_SPECIFIC"
    assert shares[0]["dimensions"][0]["member"] == "aapl:CommonStockMember"
    assert shares[0]["weighted_average_substitution"] is False
    assert "EntityCommonStockSharesOutstanding" in shares[0]["source_quote"]


def test_exact_point_and_source_acceptance_cutoff():
    rows = parse(accepted_at="2019-10-31T16:30:00-04:00")["identity_anchors"]
    assert (
        m.resolve_point(rows, "AAPL", "2019-10-31", as_of="2019-10-31T20:30:00Z")["known_at"]
        == "2019-10-31T20:30:00Z"
    )
    for day, cutoff in [
        ("2019-10-30", None),
        ("2020-01-01", None),
        ("2019-10-31", "2019-10-31T20:29:59Z"),
    ]:
        with pytest.raises(m.IdentityEvidenceError, match="NOT_ESTABLISHED"):
            m.resolve_point(rows, "AAPL", day, as_of=cutoff)
    with pytest.raises(m.IdentityEvidenceError, match="NOT_ESTABLISHED"):
        m.resolve_point(
            parse()["identity_anchors"], "AAPL", "2019-10-31", as_of="2026-01-01T00:00:00Z"
        )


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"expected_sha256": "0" * 64}, "HASH_MISMATCH"),
        ({"accepted_at": "2019-10-31T16:30:00"}, "TIMEZONE"),
        ({"source_url": "https://example.com/main.htm"}, "OFFICIAL_SOURCE"),
    ],
)
def test_source_proof_fails_closed(overrides, reason):
    with pytest.raises(m.IdentityEvidenceError, match=reason):
        parse(**overrides)


@pytest.mark.parametrize("value,unit", [("1,23", "shares"), ("123", "USD"), ("1.5", "shares")])
def test_invalid_shares_are_quarantined(value, unit):
    result = parse(fixture(value, unit, "0"))
    assert result["outstanding_shares"] == []
    assert result["unsupported_reasons"]


def test_ambiguity_and_interval_tampering():
    anchor = parse()["identity_anchors"][0]
    with pytest.raises(m.IdentityEvidenceError, match="AMBIGUOUS"):
        m.resolve_point([anchor, {**anchor, "exchange_name": "NYSE"}], "AAPL", "2019-10-31")
    with pytest.raises(m.IdentityEvidenceError, match="TAMPERED"):
        m.resolve_point([{**anchor, "valid_from": "2000-01-01"}], "AAPL", "2019-10-31")


def test_exchange_context_requires_same_class_and_period():
    base = {
        "cik": "0000320193",
        "instant": None,
        "period_start": "2019-01-01",
        "period_end": "2019-12-31",
        "typed_dimensions": False,
        "dimensions": [
            {"axis": "us-gaap:StatementClassOfStockAxis", "member": "us-gaap:CommonStockMember"}
        ],
    }
    extra = {"axis": "dei:EntityListingsExchangeAxis", "member": "exch:XNYS"}
    assert m._exchange_context_matches(base, {**base, "dimensions": base["dimensions"] + [extra]})
    assert not m._exchange_context_matches(base, {**base, "period_end": "2020-12-31"})
    assert not m._exchange_context_matches(
        base,
        {
            **base,
            "dimensions": [
                {
                    "axis": "us-gaap:StatementClassOfStockAxis",
                    "member": "issuer:PreferredStockMember",
                },
                extra,
            ],
        },
    )


def test_unknown_numeric_transformation_is_not_silently_interpreted():
    raw = fixture().replace(
        b'unitRef="u" scale=', b'format="ixt:numcommadecimal" unitRef="u" scale='
    )
    result = parse(raw)
    assert result["outstanding_shares"] == []
    assert "SHARES_TRANSFORMATION_UNSUPPORTED" in result["unsupported_reasons"]


def test_explicit_symbol_exchange_axis_never_matches_another_listing():
    context = {
        "cik": "0000001800",
        "instant": None,
        "period_start": "2019-01-01",
        "period_end": "2019-09-30",
        "typed_dimensions": False,
        "dimensions": [{"axis": "dei:EntityListingsExchangeAxis", "member": "exch:XNYS"}],
    }
    other = {
        **context,
        "dimensions": [{"axis": "dei:EntityListingsExchangeAxis", "member": "exch:XCHI"}],
    }
    assert m._exchange_context_matches(context, context)
    assert not m._exchange_context_matches(context, other)
