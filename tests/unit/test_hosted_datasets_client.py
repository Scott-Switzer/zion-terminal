import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "hosted_client", Path(__file__).parents[2] / "scripts/datasets_client.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.test",
        "https://user:password@example.test",
        "https://example.test/?token=bad",
    ],
)
def test_hosted_origin_rejects_credentials_and_insecure_transport(monkeypatch, origin):
    monkeypatch.setenv("ZION_PRIVATE_BASE_URL", origin)
    monkeypatch.setenv("ZION_PRIVATE_API_TOKEN", "secret")
    monkeypatch.setattr(sys, "argv", ["client", "catalog", "--hosted"])
    with pytest.raises(SystemExit):
        module.main()


def test_hosted_client_authenticates_only_fixed_https_origin(monkeypatch, capsys):
    calls = []

    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            return io.BytesIO(json.dumps({"sources": []}).encode())

    monkeypatch.setattr(module, "build_opener", lambda handler: Opener())
    monkeypatch.setenv("ZION_PRIVATE_BASE_URL", "https://example.test")
    monkeypatch.setenv("ZION_PRIVATE_API_TOKEN", "secret")
    monkeypatch.setattr(sys, "argv", ["client", "catalog", "--hosted"])
    module.main()
    assert calls[0].full_url == "https://example.test/private/v1/datasets"
    assert calls[0].get_header("Authorization") == "Bearer secret"
    assert "secret" not in capsys.readouterr().out
