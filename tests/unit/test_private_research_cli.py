import ast
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
cli = importlib.import_module("zion_terminal.private_research")


class Response:
    status = 200

    def __init__(self, value=None, raw=None):
        self.raw = (
            json.dumps(value or {"observations": [{"value_decimal": "9007199254740993"}]}).encode()
            if raw is None
            else raw
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, maximum):
        return self.raw[:maximum]


def test_route_exact_json_and_credential_never_output(monkeypatch):
    calls = []

    def open_request(request, timeout):
        calls.append(request)
        return Response()

    monkeypatch.setattr(cli, "open_request", open_request)
    result = CliRunner().invoke(
        cli.research,
        ["history", "--cik", "0000320193", "--metrics", "revenue"],
        env={"ZION_PRIVATE_API_TOKEN": "test-private-secret"},
    )
    assert result.exit_code == 0, result.output
    assert "9007199254740993" in result.output and "test-private-secret" not in result.output
    assert "/private/v1/financial-history?cik=0000320193&metrics=revenue" in calls[0].full_url
    assert calls[0].get_header("Authorization") == "Bearer test-private-secret"


def test_security_and_response_budget(monkeypatch):
    runner = CliRunner()
    for origin in (
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/path",
    ):
        result = runner.invoke(
            cli.research,
            ["metrics", "--base-url", origin],
            env={"ZION_PRIVATE_API_TOKEN": "secret"},
        )
        assert result.exit_code != 0
        assert "secret" not in result.output
    monkeypatch.setattr(
        cli, "open_request", lambda *args, **kwargs: Response(raw=b"x" * (cli.MAX_RESPONSE + 1))
    )
    result = runner.invoke(cli.research, ["metrics"], env={"ZION_PRIVATE_API_TOKEN": "secret"})
    assert result.exit_code != 0 and "RESPONSE_BUDGET" in result.output
    assert "secret" not in result.output


def test_error_sanitized_and_unknown_options(monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPError("https://secret@example.com", 403, "secret", {}, None)

    monkeypatch.setattr(cli, "open_request", fail)
    result = CliRunner().invoke(
        cli.research, ["coverage", "--cik", "0000320193"], env={"ZION_PRIVATE_API_TOKEN": "secret"}
    )
    assert result.exit_code != 0 and "HTTP_403" in result.output and "secret" not in result.output
    assert CliRunner().invoke(cli.research, ["history", "--sql", "SELECT"]).exit_code != 0


def test_cli_registration_is_additive():
    path = Path(__file__).resolve().parents[2] / "src/zion_terminal/cli.py"
    tree = ast.parse(path.read_text())
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "zion_terminal.private_research"
        for node in tree.body
    )
    assert any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value) == "main.add_command(research)"
        for node in tree.body
    )


def test_jobs_parameter_and_bounded_pagination(monkeypatch):
    calls = []

    def opened(request, timeout):
        calls.append(request.full_url)
        return Response({"release_id": "fixed", "next_cursor": "repeated", "matches": []})

    monkeypatch.setattr(cli, "open_request", opened)
    runner = CliRunner()
    result = runner.invoke(
        cli.research, ["jobs", "--job-id", "abc"], env={"ZION_PRIVATE_API_TOKEN": "secret"}
    )
    assert result.exit_code == 0 and calls[-1].endswith("?job_id=abc")
    result = runner.invoke(
        cli.research, ["screen", "--all-pages"], env={"ZION_PRIVATE_API_TOKEN": "secret"}
    )
    assert result.exit_code != 0 and "CURSOR_REPEATED" in result.output
    assert '"matches"' not in result.output
    assert "expected_release_id=fixed" in calls[-1]


def test_no_redirect_handler_and_dotenv_no_interpolation(tmp_path):
    import pytest

    with pytest.raises(HTTPError):
        cli.NoRedirect().redirect_request(
            SimpleNamespace(full_url="https://example.com"),
            None,
            302,
            "",
            {},
            "https://other.example.com",
        )
    path = tmp_path / "credentials.env"
    path.write_text('export ZION_PRIVATE_API_TOKEN="literal-$HOME"\n# ignored\n')
    assert cli.local_credentials(path)["ZION_PRIVATE_API_TOKEN"] == "literal-$HOME"
