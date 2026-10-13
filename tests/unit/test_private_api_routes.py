import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))


class Chunk:
    def __init__(self, value):
        self.value, self.byteLength = value, len(value)

    def to_py(self):
        return self.value


class Reader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.cancelled = False

    async def read(self):
        chunk = next(self.chunks, None)
        return SimpleNamespace(
            done=chunk is None, value=Chunk(chunk) if chunk is not None else None
        )

    async def cancel(self):
        self.cancelled = True

    def releaseLock(self):
        pass


class Request:
    def __init__(self, path="/private/v1/query", token="test-token", chunks=None, **headers):
        self.url = "https://example.test" + path
        self.method = "GET" if path.endswith("capabilities") else "POST"
        self.headers = {
            "authorization": "Bearer " + token,
            "content-type": "application/json",
            **headers,
        }
        self.reader = Reader(
            chunks if chunks is not None else [b'{"operation":"identity","symbol":"AAPL"}']
        )
        self.body = SimpleNamespace(getReader=lambda: self.reader)


@pytest.fixture
def worker(monkeypatch):
    class Response:
        def __init__(self, body, status=200, headers=None):
            self.value, self.status, self.headers = json.loads(body), status, headers or {}

    monkeypatch.setitem(
        sys.modules, "workers", SimpleNamespace(WorkerEntrypoint=object, Response=Response)
    )
    spec = importlib.util.spec_from_file_location(
        "private_route_worker", ROOT / "deploy/cloudflare-query/src/thin_worker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.Default()
    instance.env = SimpleNamespace(ZION_PRIVATE_API_TOKEN="test-token")
    return instance


def fetch(worker, request):
    return asyncio.run(worker.fetch(request))


@pytest.mark.parametrize("token,status", [("", 401), ("wrong", 401)])
def test_auth_rejects_before_body_or_storage(worker, token, status):
    request = Request(token=token)
    request.body = None
    response = fetch(worker, request)
    assert response.status == status
    assert response.headers["cache-control"] == "private, no-store"
    assert "test-token" not in json.dumps(response.value)


def test_missing_runtime_secret_is_unavailable(worker):
    worker.env = SimpleNamespace()
    assert fetch(worker, Request()).status == 503


def test_private_capabilities_are_honest_and_authenticated(worker):
    response = fetch(worker, Request("/private/v1/capabilities"))
    assert response.status == 200
    assert response.value["operations"] == ["identity", "financials", "revenue_growth"]
    assert response.value["acquisition_enabled"] is False
    assert "estimates" in response.value["pending"]


def test_query_delegates_with_no_store_headers(worker, monkeypatch):
    import private_http

    async def query(env, body, *, request_id):
        assert body == {"operation": "identity", "symbol": "AAPL"}
        return {"schema_version": "zion-private-query-v1"}

    monkeypatch.setattr(private_http, "query_existing", query)
    response = fetch(worker, Request())
    assert response.status == 200
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "chunks,status",
    [
        ([b"x" * 32769], 413),
        ([b"x" * 20000, b"y" * 20000], 413),
        ([b"{"], 400),
        ([b"[]"], 400),
        ([b'{"operation":"identity","operation":"financials"}'], 400),
    ],
)
def test_streamed_body_limits_and_json_validation(worker, chunks, status):
    request = Request(chunks=chunks)
    response = fetch(worker, request)
    assert response.status == status
    if status == 413:
        assert request.reader.cancelled


def test_query_error_is_sanitized(worker, monkeypatch):
    import private_http

    async def query(*args, **kwargs):
        raise RuntimeError("secret token and gold/private/path")

    monkeypatch.setattr(private_http, "query_existing", query)
    response = fetch(worker, Request())
    assert response.status == 503
    assert "gold/" not in json.dumps(response.value)


def test_legacy_capabilities_still_use_frozen_registry(worker):
    request = Request("/v2/capabilities")
    request.method = "GET"
    # V1 discovery is data-independent and its legacy fields must remain unchanged.
    request.url = "https://example.test/v1/capabilities"
    response = fetch(worker, request)
    assert response.status == 200
    assert "get_fundamentals" in response.value["tools"]


def test_unknown_operation_is_invalid(worker):
    response = fetch(worker, Request(chunks=[b'{"operation":"collect","symbol":"AAPL"}']))
    assert response.status == 400
    assert response.value["error"]["code"] == "INVALID_REQUEST"


def test_declared_oversize_and_wrong_content_type_fail_before_read(worker):
    response = fetch(worker, Request(**{"content-length": "32769"}))
    assert response.status == 413
    response = fetch(worker, Request(**{"content-type": "text/plain"}))
    assert response.status == 415
