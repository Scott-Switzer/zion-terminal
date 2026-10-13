import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))


@pytest.fixture
def proxy(monkeypatch):
    class Response:
        def __init__(self, body, status=200, headers=None):
            self.body, self.status, self.headers = body, status, headers or {}

    class Request:
        def __init__(self, url, **options):
            self.url, self.options, self.js_object = url, options, self

    monkeypatch.setitem(sys.modules, "workers", SimpleNamespace(Request=Request, Response=Response))
    import private_dataset_proxy

    return private_dataset_proxy


def request(
    token="test-token",
    method="GET",
    path="/private/v1/datasets/research/objects?prefix=raw%2Fwrds%2F",
):
    return SimpleNamespace(
        url="https://example.test" + path,
        method=method,
        headers={
            "authorization": "Bearer " + token,
            "if-match": '"v1"',
            "cookie": "private-cookie",
        },
    )


def test_auth_fails_before_private_binding(proxy):
    result = asyncio.run(
        proxy.handle(SimpleNamespace(ZION_PRIVATE_API_TOKEN="test-token"), request("bad"))
    )
    assert result.status == 401


def test_streams_native_bytes_and_strips_credentials(proxy):
    calls = []
    body = object()

    async def fetch(req):
        calls.append(req)
        return SimpleNamespace(
            body=body,
            status=206,
            headers={
                "content-type": "application/octet-stream",
                "etag": '"v1"',
                "content-range": "bytes 0-3/5000000",
            },
        )

    env = SimpleNamespace(
        ZION_PRIVATE_API_TOKEN="test-token", PRIVATE_DATASETS=SimpleNamespace(fetch=fetch)
    )
    result = asyncio.run(proxy.handle(env, request()))
    assert result.body is body
    assert result.status == 206
    assert (
        calls[0].url
        == "https://zion-datasets.internal/v1/datasets/research/objects?prefix=raw%2Fwrds%2F"
    )
    assert calls[0].options["headers"] == {"if-match": '"v1"'}
    assert result.headers["cache-control"] == "private, no-store"
    assert result.headers["vary"] == "Authorization"


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("POST", "/private/v1/datasets", 405),
        ("GET", "/private/v1/datasets-extra", 404),
    ],
)
def test_rejects_unknown_paths_and_writes_without_binding(proxy, method, path, status):
    env = SimpleNamespace(ZION_PRIVATE_API_TOKEN="test-token")
    assert asyncio.run(proxy.handle(env, request(method=method, path=path))).status == status


def test_sql_principal_is_server_set_and_not_forwarded_from_caller(proxy):
    calls = []

    async def fetch(req):
        calls.append(req)
        return SimpleNamespace(body=b"{}", status=200, headers={"content-type": "application/json"})

    env = SimpleNamespace(
        ZION_PRIVATE_API_TOKEN="test-token", PRIVATE_DATASETS=SimpleNamespace(fetch=fetch)
    )
    req = request(path="/private/v1/datasets/financial-query?template=sec_concept_history_v1")
    req.headers["x-zion-principal"] = "forged-role"
    asyncio.run(proxy.handle(env, req))
    assert calls[0].options["headers"]["x-zion-principal"] == "private-owner"
    assert "authorization" not in calls[0].options["headers"]
