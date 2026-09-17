from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "deploy" / "cloudflare-query" / "src"))

from temporal_core import TemporalError, normalize_date_only, parse_instant, parse_local_civil, us_equity_session, validate_leakage
from zion_terminal.temporal import parse_instant as package_parse_instant


VECTORS = json.loads((ROOT / "contracts" / "financial-temporal-v1-vectors.json").read_text())["vectors"]


def test_shared_golden_vectors_and_round_trip():
    assert len(VECTORS) == 12
    for vector in VECTORS:
        kind = vector["kind"]
        if kind == "instant":
            parsed = parse_instant(vector["input"])
            package = package_parse_instant(vector["input"])
            assert parsed.iso_utc == vector["iso_utc"]
            assert parsed.epoch_ns == vector["epoch_ns"]
            assert parsed.precision == vector["precision"]
            assert (package.iso_utc, package.epoch_ns, package.precision) == (parsed.iso_utc, parsed.epoch_ns, parsed.precision)
            assert parse_instant(parsed.iso_utc).epoch_ns == parsed.epoch_ns
        elif kind == "date_only":
            parsed = normalize_date_only(vector["input"], policy=vector["policy"])
            assert parsed.iso_utc == vector["iso_utc"]
            assert parsed.precision == "date"
        elif kind in {"reject", "local_reject"}:
            parser = lambda: parse_local_civil(vector["input"], vector["timezone"]) if kind == "local_reject" else parse_instant(vector["input"])
            try:
                parser()
            except TemporalError:
                pass
            else:
                raise AssertionError(f"vector was accepted: {vector['id']}")
        elif kind == "equivalent":
            assert parse_instant(vector["inputs"][0]).epoch_ns == parse_instant(vector["inputs"][1]).epoch_ns
        else:
            raise AssertionError(f"unhandled vector kind: {kind}")


def test_strict_as_of_rejects_date_and_naive_values():
    from serving_v2 import _as_of

    for value in ("2026-09-17", "2026-09-17T20:00:00", "garbage", ""):
        try:
            _as_of(value)
        except Exception as error:
            assert "INVALID_REQUEST" in str(error)
        else:
            raise AssertionError(f"accepted invalid as_of: {value}")
    assert _as_of("2026-09-17T16:00:00-04:00") == _as_of("2026-09-17T20:00:00Z")


def test_date_only_policy_is_conservative_for_pit():
    available = normalize_date_only("2026-09-17")
    assert available.iso_utc == "2026-09-17T23:59:59.999999999Z"
    validate_leakage([{"available_at": available.iso_utc}], "2026-09-17T23:59:59.999999999Z")
    try:
        validate_leakage([{"available_at": available.iso_utc}], "2026-09-17T12:00:00Z")
    except TemporalError:
        pass
    else:
        raise AssertionError("date-only observation leaked before conservative boundary")


def test_dst_nonexistent_and_ambiguous_times_are_rejected():
    for value in ("2026-03-08T02:30:00", "2026-11-01T01:30:00"):
        try:
            parse_local_civil(value, "America/New_York")
        except TemporalError:
            pass
        else:
            raise AssertionError(f"DST-disallowed local time was accepted: {value}")


def test_us_equity_session_calendar_and_market_local_metadata():
    assert us_equity_session("2026-09-17").regular_close == "16:00:00"
    assert us_equity_session("2026-11-27").is_early_close
    assert us_equity_session("2026-07-04").is_holiday
    assert us_equity_session("2026-09-19").is_holiday


def test_derived_availability_cannot_precede_inputs():
    validate_leakage([{"available_at": "2026-09-17T20:00:00Z", "input_available_at": ["2026-09-17T19:00:00Z"]}], "2026-09-17T20:00:00Z")
    try:
        validate_leakage([{"available_at": "2026-09-17T19:00:00Z", "input_available_at": ["2026-09-17T20:00:00Z"]}], "2026-09-17T20:00:00Z")
    except TemporalError:
        pass
    else:
        raise AssertionError("derived observation preceded an input")
