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


def test_native_rows_require_explicit_private_output(monkeypatch):
    monkeypatch.setenv("ZION_PRIVATE_BASE_URL", "https://example.test")
    monkeypatch.setenv("ZION_PRIVATE_API_TOKEN", "secret")
    monkeypatch.setattr(
        sys, "argv", ["client", "rows", "research", "--hosted", "--path", "table.parquet"]
    )
    with pytest.raises(SystemExit):
        module.main()


def test_native_rows_are_saved_privately_without_stdout_values(monkeypatch, capsys, tmp_path):
    calls = []

    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            return io.BytesIO(b'{"data":{"rows":[{"value":"private-financial-value"}]}}')

    monkeypatch.setattr(module, "build_opener", lambda handler: Opener())
    monkeypatch.setenv("ZION_PRIVATE_BASE_URL", "https://example.test")
    monkeypatch.setenv("ZION_PRIVATE_API_TOKEN", "secret")
    output = tmp_path / "rows.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "rows",
            "research",
            "--hosted",
            "--path",
            "table.parquet",
            "--columns",
            "value",
            "--row-offset",
            "1000",
            "--output",
            str(output),
        ],
    )
    module.main()
    assert "private-financial-value" in output.read_text()
    assert output.stat().st_mode & 0o777 == 0o600
    assert "private-financial-value" not in capsys.readouterr().out
    assert "offset=1000" in calls[0].full_url
