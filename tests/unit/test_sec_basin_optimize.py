import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from sec_basin_optimize import MAX_REQUESTS, MAX_SCAN, Budget, digest, percentile


def test_multiset_digest_preserves_duplicates_and_nulls():
    rows = [{"x": None}, {"x": "9007199254740993"}, {"x": None}]
    assert digest(rows) == digest(list(reversed(rows)))
    assert digest(rows) != digest(rows[:-1])


def test_budget_stops_before_request():
    b = Budget()
    b.requests = MAX_REQUESTS
    with pytest.raises(RuntimeError, match="STOP_SQL_BUDGET"):
        b.reserve_query(1)
    assert b.requests == MAX_REQUESTS
    b.requests = 0
    b.scan = MAX_SCAN - 10
    with pytest.raises(RuntimeError):
        b.reserve_query(11)
    assert b.requests == 0


def test_unknown_scan_stops_and_quantiles_are_explicit():
    with pytest.raises(RuntimeError, match="STOP_UNKNOWN_SCAN"):
        Budget().record_scan({})
    assert percentile([3, 1, 2], 0.95) == 3


def test_identity_groups_are_stable_and_coarse():
    from sec_basin_optimize import coarse_group
    assert coarse_group('0000789019') == 0
    assert len({coarse_group(str(i).zfill(10)) for i in range(1000)}) == 16
    assert all(0 <= coarse_group(str(i).zfill(10)) < 16 for i in range(1000))
