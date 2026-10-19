import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from publish_historical_coverage import build_coverage

from zion_terminal.history_store import build_release


def sample(tmp_path):
    rows = [
        dict(
            series_id="x:rate",
            observation_date=f"2000-01-0{i}",
            value_decimal="0" if i in (2, 4) else None,
            missing_reason=None if i in (2, 4) else "SOURCE_EMPTY",
            unit="percent",
            asset_class="rates",
            label="Rate",
            source_id="source",
        )
        for i in range(1, 6)
    ]
    reference = build_release(rows, {"source": {"source_sha256": "a" * 64}}, tmp_path)
    return reference


def test_build_counts_zero_as_populated_and_classifies_boundary_and_interior(tmp_path):
    ref = sample(tmp_path)
    side = build_coverage(tmp_path, ref["relative_manifest"], ref["sha256"])
    assert side["release_id"] == ref["release_id"]
    assert side["manifest_sha256"] == ref["sha256"]
    c = side["series"]["x:rate"]
    assert c["valid_date_min"] == "2000-01-02"
    assert c["valid_date_max"] == "2000-01-04"
    assert c["populated_cells"] == 2
    assert c["before_first_value"] == c["after_last_value"] == c["interior_missing"] == 1
    assert c["missing_cells"] == 3 and c["total_cells"] == 5


def test_build_refuses_changed_packet_and_wrong_manifest_pin(tmp_path):
    ref = sample(tmp_path)
    with pytest.raises(ValueError):
        build_coverage(tmp_path, ref["relative_manifest"], "b" * 64)
    manifest = json.loads((tmp_path / ref["relative_manifest"]).read_bytes())
    packet = next(iter(manifest["series"].values()))
    (tmp_path / packet["local_path"]).write_bytes(b"{}")
    with pytest.raises(ValueError):
        build_coverage(tmp_path, ref["relative_manifest"], ref["sha256"])


def test_build_refuses_manifest_counts_disagreeing_with_packet(tmp_path):
    ref = sample(tmp_path)
    path = tmp_path / ref["relative_manifest"]
    manifest = json.loads(path.read_bytes())
    manifest["series"]["x:rate"]["missing"] = 1
    raw = json.dumps(manifest).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        build_coverage(tmp_path, ref["relative_manifest"], hashlib.sha256(raw).hexdigest())


def test_all_missing_series_has_no_invented_valid_window(tmp_path):
    rows = [
        dict(
            series_id="x:none",
            observation_date="2000-01-01",
            value_decimal=None,
            missing_reason="SOURCE_EMPTY",
            unit="percent",
            asset_class="rates",
            label="Absent rate",
            source_id="source",
        )
    ]
    ref = build_release(rows, {"source": {"source_sha256": "a" * 64}}, tmp_path)
    c = build_coverage(tmp_path, ref["relative_manifest"], ref["sha256"])["series"]["x:none"]
    assert c["valid_date_min"] is c["valid_date_max"] is None
    assert c["all_missing_cells"] == c["missing_cells"] == 1
    assert c["before_first_value"] == c["after_last_value"] == c["interior_missing"] == 0
