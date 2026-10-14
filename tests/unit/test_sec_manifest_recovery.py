import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from sec_foundation_recovery import bounded_manifest


class Store:
    def __init__(self, raw, size=None):
        self.raw = raw
        self.size = len(raw) if size is None else size
        self.gets = 0
        self.options = None

    def head_object(self, **kw):
        return {"ContentLength": self.size, "ETag": '"source-version"'}

    def get_object(self, **kw):
        self.gets += 1
        self.options = kw
        return {"Body": io.BytesIO(self.raw)}


def test_metadata_reader_pins_etag_checks_length_and_explicit_budget():
    s = Store(json.dumps({"parts": []}).encode())
    value, proof = bounded_manifest(s, "research", "manifest.json", 1024)
    assert value == {"parts": []} and proof["sha256"]
    assert s.options["IfMatch"] == '"source-version"'
    too_big = Store(b"{}", 2048)
    with pytest.raises(ValueError, match="BUDGET"):
        bounded_manifest(too_big, "research", "manifest.json", 1024)
    assert too_big.gets == 0
    with pytest.raises(ValueError, match="LENGTH"):
        bounded_manifest(Store(b"{}", 3), "research", "manifest.json", 1024)
