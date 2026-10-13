import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "expanded_index", Path(__file__).parents[2] / "scripts/publish_sec_expanded_index.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_index_excludes_oversized_and_ambiguous_ticker_links():
    def entry(cik, size=100):
        return {
            "name": f"CIK{cik:010d}.json",
            "uncompressed_bytes": size,
            "compressed_bytes": 50,
            "flags": 0,
            "compression": 8,
            "crc": 123,
            "offset": 0,
        }

    tickers = {
        "0": {"cik_str": 1, "ticker": "DUP"},
        "1": {"cik_str": 2, "ticker": "DUP"},
        "2": {"cik_str": 1, "ticker": "ONE"},
        "3": {"cik_str": 3, "ticker": "BIG"},
    }
    objects, result = module.build_objects(
        [entry(1), entry(2), entry(3, 5 * 1048576)], tickers, {"key": "fixed"}
    )
    assert result["counts"] == {
        "indexed_filers": 2,
        "oversized_or_unsupported": 1,
        "mapped_ticker_symbols": 1,
        "mapped_unique_issuers": 1,
        "ambiguous_tickers_excluded": 1,
    }
    assert result["key"] in objects
    assert module.sha(objects[result["key"]]) == result["sha256"]


def test_tail_read_without_size_is_bounded_not_full_archive():
    class Client:
        def get_object(self, **kwargs):
            start, end = map(int, kwargs["Range"][6:].split("-"))
            assert kwargs["IfMatch"] == "etag"
            return {"Body": io.BytesIO(b"x" * (end - start + 1))}

    reader = module.RangeFile(Client(), "archive", {"ContentLength": 1400000000, "ETag": "etag"})
    reader.seek(-22, 2)
    assert reader.read() == b"x" * 22
    reader.seek(0)
    with pytest.raises(ValueError):
        reader.read()
