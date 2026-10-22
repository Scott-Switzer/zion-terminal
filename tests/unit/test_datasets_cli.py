"""Direct native dataset requests preserve exact private source contracts."""

import importlib
import json

import click
import pytest
from click.testing import CliRunner


def module():
    return importlib.import_module("zion_terminal.datasets_cli")


def invoke(monkeypatch, tmp_path, args, page=None, env=None):
    cli = module()
    calls = []

    def fetch(base, token, route, params):
        calls.append((base, token, route, params))
        return page or {"schema_version": "test-native", "null_value": None}, 100

    monkeypatch.setattr(cli, "fetch", fetch)
    result = CliRunner().invoke(
        cli.datasets,
        ["--credential-file", str(tmp_path / "absent.env"), *args],
        env={"ZION_PRIVATE_API_TOKEN": "test-secret", **(env or {})},
    )
    return result, calls


@pytest.mark.parametrize(("args", "route", "params"), [
    ([], "datasets/catalog", {"offset": 0, "limit": 50}),
    (["catalog", "--search", "crsp", "--family", "crsp", "--limit", "7", "--offset", "3"],
     "datasets/catalog", {"search": "crsp", "family": "crsp", "offset": 3, "limit": 7}),
    (["describe", "--dataset-id", "a" * 32], "datasets/catalog/" + "a" * 32, {}),
    (["objects", "--store", "research", "--prefix", "raw/wrds/", "--cursor", "opaque"],
     "datasets/research/objects", {"prefix": "raw/wrds/", "limit": 50, "cursor": "opaque"}),
    (["schema", "--store", "lakehouse", "--path", "namespace/data.parquet"],
     "datasets/lakehouse/schema", {"path": "namespace/data.parquet"}),
    (["rows", "--store", "financial", "--path", "normalized/data.parquet", "--columns",
      "amount,units", "--offset", "20", "--limit", "1000"],
     "datasets/financial/rows", {"path": "normalized/data.parquet", "columns": "amount,units",
                                  "offset": 20, "limit": 1000}),
    (["rows", "--store", "filings", "--path", "corpus/data.parquet", "--cursor", "opaque"],
     "datasets/filings/rows", {"path": "corpus/data.parquet", "cursor": "opaque", "limit": 50}),
])
def test_exact_routing_and_no_bearer_logging(monkeypatch, tmp_path, args, route, params):
    result, calls = invoke(monkeypatch, tmp_path, args)
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][1:] == ("test-secret", route, params)
    assert "test-secret" not in result.output


def test_native_exact_values_lineage_and_exclusive_output(monkeypatch, tmp_path):
    page = {
        "schema_version": "zion-native-table-v1",
        "source": {"etag": "source-version", "PIT_status": "NOT_ESTABLISHED"},
        "columns": [{"name": "amount", "type": "DECIMAL", "scale": 4}],
        "rows": [{"amount": "9007199254740993.1200", "unit": "source units", "other": None}],
    }
    output = tmp_path / "native.json"
    args = ["rows", "--store", "research", "--path", "data.parquet", "--output", str(output)]
    result, _ = invoke(monkeypatch, tmp_path, args, page)
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_bytes()) == page
    assert json.loads(result.output) == page
    again, _ = invoke(monkeypatch, tmp_path, args, page)
    assert again.exit_code != 0
    assert json.loads(output.read_bytes()) == page


@pytest.mark.parametrize("args", [
    ["describe"], ["describe", "--dataset-id", "../escape"],
    ["rows", "--store", "research"],
    ["rows", "--path", "data.parquet"],
    ["rows", "--store", "research", "--path", "../secret"],
    ["rows", "--store", "research", "--path", "/secret"],
    ["rows", "--store", "research", "--path", "a/../secret"],
    ["rows", "--store", "research", "--path", "a\\secret"],
    ["objects", "--store", "research", "--prefix", "a\nsecret"],
    ["rows", "--store", "research", "--path", "data", "--cursor", "x", "--offset", "0"],
    ["rows", "--store", "research", "--path", "data", "--columns", "x,x"],
    ["catalog", "--limit", "201"],
    ["schema", "--store", "research", "--path", "data", "--columns", "x"],
    ["catalog", "--base-url", "http://example.test"],
    ["catalog", "--base-url", "https://user:secret@example.test"],
    ["catalog", "--base-url", "https://example.test/path"],
    ["catalog", "--base-url", "https://example.test?token=secret"],
    ["catalog", "--base-url", "https://example.test:invalid"],
    ["catalog", "--base-url", "https://example.test/#fragment"],
])
def test_invalid_requests_refuse_before_transport(monkeypatch, tmp_path, args):
    result, calls = invoke(monkeypatch, tmp_path, args)
    assert result.exit_code != 0
    assert not calls
    assert "test-secret" not in result.output


def test_existing_credential_file_is_reused_without_exposure(monkeypatch, tmp_path):
    cli = module()
    credential = tmp_path / "private.env"
    credential.write_text(
        "ZION_PRIVATE_API_TOKEN=file-secret\nZION_PRIVATE_BASE_URL=https://source.test\n"
    )
    calls = []
    monkeypatch.setattr(cli, "fetch", lambda *args: calls.append(args) or ({"entries": []}, 10))
    monkeypatch.delenv("ZION_PRIVATE_API_TOKEN", raising=False)
    monkeypatch.delenv("ZION_PRIVATE_BASE_URL", raising=False)
    result = CliRunner().invoke(cli.datasets, ["--credential-file", str(credential)])
    assert result.exit_code == 0, result.output
    assert calls[0][:2] == ("https://source.test", "file-secret")
    assert "file-secret" not in result.output


def test_sanitized_transport_error_is_preserved(monkeypatch, tmp_path):
    cli = module()

    def refused(*args):
        raise click.ClickException("PRIVATE_API_HTTP_403")

    monkeypatch.setattr(cli, "fetch", refused)
    result = CliRunner().invoke(
        cli.datasets, ["--credential-file", str(tmp_path / "none")],
        env={"ZION_PRIVATE_API_TOKEN": "test-secret"},
    )
    assert result.exit_code != 0
    assert "PRIVATE_API_HTTP_403" in result.output
    assert "test-secret" not in result.output


@pytest.mark.parametrize("token", ["", "token\r\nInjected: header"])
def test_missing_or_malformed_credentials_refuse_before_transport(monkeypatch, tmp_path, token):
    result, calls = invoke(
        monkeypatch, tmp_path, [], env={"ZION_PRIVATE_API_TOKEN": token}
    )
    assert result.exit_code != 0
    assert not calls
    assert "Injected" not in result.output


def test_native_row_cursor_matches_existing_16384_source_budget(monkeypatch, tmp_path):
    # Parser compatibility only; actual public URLs remain subject to edge limits.
    cursor = "x" * 16384
    result, calls = invoke(monkeypatch, tmp_path, [
        "rows", "--store", "research", "--path", "data.parquet", "--cursor", cursor
    ])
    assert result.exit_code == 0, result.output
    assert calls[0][3]["cursor"] == cursor
    assert "offset" not in calls[0][3]


@pytest.mark.parametrize(("action", "length"), [("rows", 16385), ("objects", 8193)])
def test_cursor_budgets_still_refuse_before_transport(monkeypatch, tmp_path, action, length):
    args = [action, "--store", "research", "--cursor", "x" * length]
    if action == "rows":
        args.extend(["--path", "data.parquet"])
    result, calls = invoke(monkeypatch, tmp_path, args)
    assert result.exit_code != 0
    assert not calls
