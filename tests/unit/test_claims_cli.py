"""Claims CLI makes exact pinned requests and refuses unsafe or conflicting responses."""

import importlib
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest
from click.testing import CliRunner

research = importlib.import_module("zion_terminal.private_research")
ENGINE = "a" * 64
SOURCE = "b" * 64
SECRET = "private-test-credential"
ARGS = [
    "--cik",
    "0000320193",
    "--as-of",
    "2026-09-24",
    "--release-id",
    ENGINE,
    "--source-release-id",
    SOURCE,
]


def response_body():
    return {
        "schema_version": "zion-equity-claims-v1",
        "release_id": ENGINE,
        "source_release_id": SOURCE,
        "source_bundle": {"issuer": {"cik": "0000320193"}},
        "claims": {"debt": {"value_decimal": None, "evidence_state": "UNAVAILABLE"}},
    }


class Response:
    def __init__(self, value):
        self.raw = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, maximum):
        return self.raw[:maximum]


def invoke(args=None, **kwargs):
    module = importlib.import_module("zion_terminal.claims_cli")
    return CliRunner().invoke(
        module.claims,
        ARGS if args is None else args,
        env={"ZION_PRIVATE_API_TOKEN": SECRET},
        **kwargs,
    )


def test_claims_cli_sends_exact_dual_pins_and_preserves_null_without_printing_token(
    monkeypatch, tmp_path
):
    calls = []

    def open_request(request, timeout):
        calls.append(request)
        return Response(response_body())

    monkeypatch.setattr(research, "open_request", open_request)
    output = tmp_path / "claims.json"
    result = invoke(ARGS + ["--base-url", "https://example.com", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0].get_header("Authorization") == "Bearer " + SECRET
    parsed = urlparse(calls[0].full_url)
    assert parsed.path == "/private/v1/equity-claims"
    assert parse_qs(parsed.query) == {
        "cik": ["0000320193"],
        "as_of": ["2026-09-24"],
        "release_id": [ENGINE],
        "expected_release_id": [ENGINE],
        "source_release_id": [SOURCE],
        "expected_source_release_id": [SOURCE],
    }
    assert json.loads(output.read_text()) == response_body()
    assert json.loads(result.output)["claims"]["debt"]["value_decimal"] is None
    assert SECRET not in result.output + output.read_text()


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--cik", "320193"),
        ("--cik", "１２３４５６７８９０"),
        ("--as-of", "2026-02-30"),
        ("--as-of", "2026-09-24T00:00:00Z"),
        ("--release-id", "wrong"),
        ("--source-release-id", "../manifest.json"),
        ("--base-url", "http://example.com"),
        ("--base-url", "https://user:pass@example.com"),
        ("--base-url", "https://example.com/path"),
    ],
)
def test_claims_cli_refuses_invalid_inputs_before_network(monkeypatch, flag, value):
    monkeypatch.setattr(research, "open_request", lambda *a, **kw: pytest.fail("must not fetch"))
    args = list(ARGS)
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args += [flag, value]
    result = invoke(args)
    assert result.exit_code != 0
    assert SECRET not in result.output


def test_claims_cli_sanitizes_http_refusal_and_does_not_export(monkeypatch, tmp_path):
    def refused(*args, **kwargs):
        raise HTTPError("https://" + SECRET + "@example.com", 403, SECRET, {}, None)

    monkeypatch.setattr(research, "open_request", refused)
    output = tmp_path / "refused.json"
    result = invoke(ARGS + ["--output", str(output)])
    assert result.exit_code != 0
    assert "PRIVATE_API_HTTP_403" in result.output
    assert SECRET not in result.output
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["engine", "source", "issuer", "schema", "error"])
def test_claims_cli_refuses_conflicting_or_error_responses_before_export(
    monkeypatch, tmp_path, mutation
):
    value = response_body()
    if mutation == "engine":
        value["release_id"] = "c" * 64
    elif mutation == "source":
        value["source_release_id"] = "c" * 64
    elif mutation == "issuer":
        value["source_bundle"]["issuer"]["cik"] = "0000789019"
    elif mutation == "schema":
        value["schema_version"] = "other"
    else:
        value = {"error": {"message": SECRET}}
    monkeypatch.setattr(research, "open_request", lambda *a, **kw: Response(value))
    output = tmp_path / "conflicting.json"
    result = invoke(ARGS + ["--output", str(output)])
    assert result.exit_code != 0
    assert SECRET not in result.output
    assert not output.exists()


def test_root_cli_registers_working_claims_command(monkeypatch):
    from zion_terminal.cli import main

    monkeypatch.setattr(research, "open_request", lambda *a, **kw: Response(response_body()))
    result = CliRunner().invoke(main, ["claims"] + ARGS, env={"ZION_PRIVATE_API_TOKEN": SECRET})
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["release_id"] == ENGINE
