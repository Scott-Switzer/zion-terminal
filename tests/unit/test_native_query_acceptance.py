import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

spec = importlib.util.spec_from_file_location(
    "native_acceptance", Path(__file__).parents[2] / "scripts/native_query_acceptance.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(empty=False):
    def request(base, method, path, payload, token):
        if not token:
            return 401, {}
        u = urlparse(path)
        q = parse_qs(u.query)
        if u.path.endswith("/catalog"):
            return 200, {
                "registered_releases": 805,
                "unique_datasets": 803,
                "object_references": 41485,
                "release_id": "c" * 64,
                "entries": [{"dataset": q.get("search", ["example"])[0], "id": "a" * 32}],
            }
        if "/catalog/" in u.path:
            return 200, {"store": "research", "objects": [{"key": "private-path.parquet"}]}
        if u.path.endswith("/schema"):
            return 200, {
                "data": {
                    "columns": [
                        {"name": x} for x in ["date", "mktrf", "ticker", "shpct", "permno", "ret"]
                    ]
                }
            }
        columns = q["columns"][0].split(",")
        if q.get("cursor") and q["columns"][0] in ("mktrf,date", "shpct,ticker", "ret,date,permno"):
            return 400, {"error": {"code": "CURSOR_SCOPE_MISMATCH"}}
        return 200, {
            "quality": "SOURCE_UNVALIDATED",
            "cache": "HIT",
            "next_cursor": "opaque",
            "data": {
                "row_offset": 3 if q.get("cursor") else 0,
                "rows": [] if empty else [{k: "private-financial-value" for k in columns}] * 3,
            },
        }

    return request


def test_native_report_checks_queries_without_logging_data_or_credentials():
    report = module.run("https://example.test", "secret-token", request_fn=fixture())
    assert report["status"] == "PASS"
    assert len(report["checks"]) == 23
    assert not any(
        x in json.dumps(report) for x in ("secret-token", "private-path", "private-financial-value")
    )


def test_empty_native_rows_do_not_count_as_success():
    assert (
        module.run("https://example.test", "secret-token", request_fn=fixture(True))["status"]
        == "FAIL"
    )
