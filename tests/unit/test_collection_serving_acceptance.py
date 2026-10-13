import importlib.util
import io
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "collection_acceptance", Path(__file__).parents[2] / "scripts/collection_serving_acceptance.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_acceptance_follows_empty_and_zero_byte_pages_without_leaking_source_data(monkeypatch):
    class Response(io.BytesIO):
        def __init__(self, raw, status=200, headers=None):
            super().__init__(raw)
            self.code = status
            self.headers = {"cache-control": "private, no-store", **(headers or {})}

    class Opener:
        def open(self, req, timeout):
            u = urlparse(req.full_url)
            q = parse_qs(u.query)
            if not req.has_header("Authorization"):
                return Response(b"{}", 401)
            if u.path == "/private/v1/datasets":
                return Response(
                    json.dumps(
                        {
                            "sources": [
                                {"id": s} for s in ("financial", "research", "filings", "lakehouse")
                            ]
                        }
                    ).encode()
                )
            if u.path.endswith("/objects"):
                cursor = q.get("cursor", [""])[0]
                value = {"objects": [], "truncated": True, "cursor": "next"}
                if cursor == "next":
                    value = {
                        "objects": [{"path": "empty", "bytes": 0}],
                        "truncated": True,
                        "cursor": "last",
                    }
                if cursor == "last":
                    value = {
                        "objects": [{"path": "private-source", "bytes": 10000}],
                        "truncated": False,
                        "cursor": None,
                    }
                return Response(json.dumps(value).encode())
            if req.method == "HEAD":
                return Response(b"", headers={"etag": '"v1"'})
            if req.get_header("If-match") == '"invalid-version"':
                return Response(b"{}", 412)
            return Response(b"private-data", 206)

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda handler: Opener())
    report = module.run("https://example.test", "secret-token")
    assert report["status"] == "PASS"
    assert len(report["checks"]) == 26
    encoded = json.dumps(report)
    assert "secret-token" not in encoded
    assert "private-source" not in encoded
    assert "private-data" not in encoded
