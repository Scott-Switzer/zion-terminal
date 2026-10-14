import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
from sec_screener_http import handle


def test_large_or_invalid_decimal_threshold_rejected_before_engine_or_storage():
    env = SimpleNamespace(ZION_PRIVATE_API_TOKEN="owned")
    for value in ["1e999999999", "abc", "Infinity", "NaN"]:
        request = SimpleNamespace(
            method="GET",
            url="https://owned/private/v1/financial-screen?minimum=" + value,
            headers={"authorization": "Bearer owned"},
        )
        result, status = asyncio.run(handle(env, request))
        assert status == 422 and result["error"]["code"] == "INVALID_SCREEN_THRESHOLD"


def test_auth_precedes_threshold_validation():
    env = SimpleNamespace(ZION_PRIVATE_API_TOKEN="owned")
    request = SimpleNamespace(
        method="GET", url="https://owned/private/v1/financial-screen?minimum=NaN", headers={}
    )
    _, status = asyncio.run(handle(env, request))
    assert status == 401
