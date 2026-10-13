import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from private_financials import PrivateFinancialsError  # noqa: E402
from sec_expanded import decode_member, normalize_companyfacts  # noqa: E402


def company():
    return {
        "cik": 1234,
        "entityName": "Fixture Co",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 100,
                                "filed": "2024-02-01",
                                "accn": "a",
                                "form": "10-K",
                            },
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 110,
                                "filed": "2024-03-01",
                                "accn": "b",
                                "form": "10-K/A",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 120,
                                "filed": "2025-02-01",
                                "accn": "c",
                                "form": "10-K",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-09-30",
                                "val": 90,
                                "filed": "2024-11-01",
                                "accn": "d",
                                "form": "10-Q",
                            },
                        ]
                    }
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-12-31",
                                "val": 20,
                                "filed": "2025-02-01",
                                "accn": "c",
                                "form": "10-K",
                            },
                            {
                                "end": "2024-09-30",
                                "val": 15,
                                "filed": "2024-11-01",
                                "accn": "d",
                                "form": "10-Q",
                            },
                        ]
                    }
                },
            }
        },
    }


def run(data=None, **kwargs):
    return normalize_companyfacts(
        data or company(),
        cik="0000001234",
        metrics=["revenue", "cash"],
        period="annual",
        lookback=10,
        as_of="2025-03-01T00:00:00Z",
        **kwargs,
    )


def test_native_facts_have_explicit_concepts_units_and_conservative_dates():
    result = run()
    revenues = [r for r in result["observations"] if r["metric"] == "revenue"]
    assert [r["value"] for r in revenues] == ["110", "120"]
    assert revenues[0]["concept"] == "us-gaap:Revenues"
    assert revenues[0]["availability_policy"] == "DATE_ONLY_NEXT_DAY_ET_V1"
    assert revenues[0]["available_at"].startswith("2024-03-02T")
    assert len([r for r in result["observations"] if r["metric"] == "cash"]) == 1
    assert all(r["validation_state"] == "SOURCE_UNVALIDATED" for r in result["observations"])


def test_revision_and_date_only_publication_do_not_look_ahead():
    d = normalize_companyfacts(
        company(),
        cik="0000001234",
        metrics=["revenue"],
        period="annual",
        lookback=10,
        as_of="2024-03-01T23:59:59Z",
    )
    assert [r["value"] for r in d["observations"]] == ["100"]


def test_unit_mismatch_and_missing_concepts_stay_missing():
    d = company()
    d["facts"]["us-gaap"]["Revenues"]["units"] = {
        "EUR": d["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    }
    result = normalize_companyfacts(
        d,
        cik="0000001234",
        metrics=["revenue", "capex"],
        period="annual",
        lookback=10,
        as_of="2025-03-01T00:00:00Z",
    )
    assert result["observations"] == []
    assert set(result["missing_metrics"]) == {"revenue", "capex"}


def test_conflicting_same_revision_is_retained_not_averaged():
    d = company()
    rows = d["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    rows.append({**rows[2], "val": 999})
    result = run(d)
    assert result["conflicts"]
    assert not any(
        r["period_end"] == "2024-12-31" and r["metric"] == "revenue" for r in result["observations"]
    )


def test_wrong_cik_and_nonfinite_values_are_rejected():
    with pytest.raises(PrivateFinancialsError):
        normalize_companyfacts(
            company(),
            cik="0000009999",
            metrics=["revenue"],
            period="annual",
            lookback=5,
            as_of="2025-03-01T00:00:00Z",
        )
    d = company()
    d["facts"]["us-gaap"]["Revenues"]["units"]["USD"][2]["val"] = float("nan")
    with pytest.raises(PrivateFinancialsError):
        run(d)


def test_quarterly_does_not_relabel_ytd():
    result = normalize_companyfacts(
        company(),
        cik="0000001234",
        metrics=["revenue"],
        period="quarterly",
        lookback=5,
        as_of="2025-03-01T00:00:00Z",
    )
    assert result["observations"] == []


def test_bounded_zip_member_integrity_and_decompression():
    import zlib

    raw = json.dumps(company()).encode()
    compressed = zlib.compress(raw)[2:-4]
    entry = {"compression": 8, "crc": zlib.crc32(raw), "uncompressed_bytes": len(raw)}
    assert decode_member(compressed, entry) == raw
    with pytest.raises(PrivateFinancialsError):
        decode_member(compressed, {**entry, "crc": 0})
    with pytest.raises(PrivateFinancialsError):
        decode_member(compressed, {**entry, "uncompressed_bytes": 5 * 1048576})
