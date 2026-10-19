from click.testing import CliRunner

import zion_terminal.stocks_cli as cli


def test_stock_prices_route_is_not_gated_by_fundamental_coverage(monkeypatch):
    calls = []

    def fetch(base, token, route, params):
        calls.append((route, params))
        return dict(
            schema_version="zion-stock-history-v1",
            release_id="a" * 64,
            prices=[dict(close="1.000000001", available_at=None)],
        ), 100

    monkeypatch.setattr(cli, "fetch", fetch)
    result = CliRunner().invoke(
        cli.stocks,
        ["--symbol", "AAPL", "--date-from", "2019-01-01", "--limit", "5"],
        env={"ZION_PRIVATE_API_TOKEN": "private-test"},
    )
    assert result.exit_code == 0, result.output
    assert calls[0][0] == "stock-history"
    assert calls[0][1] == dict(symbol="AAPL", date_from="2019-01-01", limit=5)
    assert "private-test" not in result.output
