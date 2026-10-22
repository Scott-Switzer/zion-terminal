from click.testing import CliRunner

import zion_terminal.historical_cli as cli


def test_terminal_historical_preserves_exact_request_and_export(monkeypatch, tmp_path):
    calls = []
    page = dict(
        schema_version="zion-historical-series-v1",
        release_id="a" * 64,
        point_in_time_status="NOT_ESTABLISHED",
        observations=[
            dict(observation_date="1999-01-04", value_decimal="1.1789", unit="USD per EUR")
        ],
    )

    def fetch(base, token, route, params):
        calls.append((base, token, route, params))
        return page, 100

    monkeypatch.setattr(cli, "fetch", fetch)
    path = tmp_path / "new.json"
    args = [
        "--series-id",
        "ecb:fx:USD:EUR",
        "--date-from",
        "1999-01-01",
        "--expected-release-id",
        "a" * 64,
        "--limit",
        "5",
        "--output",
        str(path),
    ]
    result = CliRunner().invoke(cli.historical, args, env={"ZION_PRIVATE_API_TOKEN": "test-secret"})
    assert result.exit_code == 0, result.output
    assert calls[0][2] == "historical-series"
    assert calls[0][3] == dict(
        series_id="ecb:fx:USD:EUR",
        date_from="1999-01-01",
        expected_release_id="a" * 64,
        limit=5,
        view="catalog",
    )
    assert "test-secret" not in result.output and "1.1789" in path.read_text()
    assert (
        CliRunner()
        .invoke(cli.historical, args, env={"ZION_PRIVATE_API_TOKEN": "test-secret"})
        .exit_code
        != 0
    )


def test_invalid_origin_or_date_never_calls_transport(monkeypatch):
    def never(*args):
        raise AssertionError("Transport must not run")

    monkeypatch.setattr(cli, "fetch", never)
    runner = CliRunner()
    for args in [
        ["--base-url", "http://bad"],
        ["--date-from", "not-a-date"],
        ["--expected-release-id", "bad"],
        ["--series-id", "../../bad"],
    ]:
        result = runner.invoke(cli.historical, args, env={"ZION_PRIVATE_API_TOKEN": "test-secret"})
        assert result.exit_code != 0 and "test-secret" not in result.output
