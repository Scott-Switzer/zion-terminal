import pytest

from zion_terminal.datasets.financial_foundation import (
    FoundationError,
    instant_ns,
    resolve_identifier,
    validate_observation,
)


def link():
    return {
        "namespace": "TICKER",
        "identifier": "TEST",
        "entity_id": "synthetic:issuer:a",
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_to": "2021-01-01T00:00:00Z",
        "valid_to_kind": "SOURCE_EXPLICIT",
        "known_at": "2020-01-02T00:00:00Z",
        "known_at_source": "SOURCE_PUBLICATION",
        "source_sha256": "a" * 64,
    }


def test_source_link_requires_both_historical_validity_and_knowledge_date():
    item = link()
    assert (
        resolve_identifier(
            [item], "TICKER", "TEST", valid_at="2020-06-01T00:00:00Z", as_of="2020-06-01T00:00:00Z"
        )["entity_id"]
        == "synthetic:issuer:a"
    )
    for valid, knowledge in [
        ("2021-01-01T00:00:00Z", "2021-01-01T00:00:00Z"),
        ("2020-01-01T00:00:00Z", "2020-01-01T12:00:00Z"),
    ]:
        with pytest.raises(FoundationError, match="NOT_ESTABLISHED"):
            resolve_identifier([item], "TICKER", "TEST", valid_at=valid, as_of=knowledge)


def test_missing_unknown_and_ambiguous_links_are_not_guessed():
    item = link()
    item["valid_from"] = None
    with pytest.raises(FoundationError):
        resolve_identifier(
            [item], "TICKER", "TEST", valid_at="2020-06-01T00:00:00Z", as_of="2020-06-01T00:00:00Z"
        )
    with pytest.raises(FoundationError, match="AMBIGUOUS"):
        resolve_identifier(
            [link(), {**link(), "entity_id": "synthetic:issuer:b"}],
            "TICKER",
            "TEST",
            valid_at="2020-06-01T00:00:00Z",
            as_of="2020-06-01T00:00:00Z",
        )
    with pytest.raises(FoundationError, match="INVALID_IDENTIFIER"):
        resolve_identifier(
            [],
            "SEC_CIK",
            "not-a-cik",
            valid_at="2020-06-01T00:00:00Z",
            as_of="2020-06-01T00:00:00Z",
        )


def observation():
    return {
        "source_observation_id": "synthetic:fact:a",
        "source_sha256": "a" * 64,
        "source_concept": "synthetic:concept",
        "native_unit": "USD",
        "unit": "USD",
        "period_end": "2020-12-31",
        "available_at": "2021-02-01T00:00:00.000000001Z",
        "availability_policy": "EXACT_SOURCE_ACCEPTANCE",
        "layer": "NORMALIZED_RESEARCH",
        "reporting_status": "SOURCE_REPORTED_VALUE",
        "value_decimal": "9007199254740993.000000000000000001",
    }


def test_nanosecond_availability_and_exact_decimal_are_preserved():
    row = observation()
    with pytest.raises(FoundationError, match="NOT_AVAILABLE"):
        validate_observation(row, as_of="2021-02-01T00:00:00.000000000Z")
    result = validate_observation(row, as_of=row["available_at"])
    assert result["value_decimal"] == row["value_decimal"]
    assert result["canonical_eligible"] is False
    assert instant_ns("2021-02-01T01:00:00+01:00") == instant_ns("2021-02-01T00:00:00Z")


def test_nulls_are_source_nulls_only_and_units_never_convert_silently():
    row = observation()
    with pytest.raises(FoundationError):
        validate_observation({**row, "value_decimal": None}, as_of=row["available_at"])
    result = validate_observation(
        {**row, "value_decimal": None, "reporting_status": "SOURCE_REPORTED_NULL"},
        as_of=row["available_at"],
    )
    assert result["value_decimal"] is None
    with pytest.raises(FoundationError, match="UNIT_CONVERSION"):
        validate_observation({**row, "unit": "EUR"}, as_of=row["available_at"])
    with pytest.raises(FoundationError, match="FINITE_DECIMAL"):
        validate_observation({**row, "value_decimal": "NaN"}, as_of=row["available_at"])
