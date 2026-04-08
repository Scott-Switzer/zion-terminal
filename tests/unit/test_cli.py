"""Tests for the CLI – uses Click's test runner, no network calls."""

import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from zion_terminal.cli import main
from zion_terminal.models.responses import OrchestratorResponse, RetrievalResult


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_orchestrator():
    """Patch _build_orchestrator to return a mock."""
    mock_orc = MagicMock()
    mock_orc.query.return_value = OrchestratorResponse(
        success=True, query="test", intent="quote",
        results=[RetrievalResult(data=[{
            "ticker": "AAPL", "price": 185.50, "source": "yahoo_finance",
        }], sources_used=["yahoo_finance"])],
    )
    mock_orc._retrieval = MagicMock()
    mock_orc._retrieval.fetch.return_value = RetrievalResult(data=[{
        "ticker": "AAPL", "price": 185.50, "source": "yahoo_finance",
    }], sources_used=["yahoo_finance"])
    mock_orc.close = MagicMock()
    return mock_orc


class TestCLI:
    def test_help(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Zion Terminal" in result.output

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.2.0" in result.output

    def test_no_subcommand_shows_help(self, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "Usage" in result.output or "Commands" in result.output

    @patch("zion_terminal.cli._build_orchestrator")
    def test_query_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["query", "Get AAPL stock price"])
        assert result.exit_code == 0

    @patch("zion_terminal.cli._build_orchestrator")
    def test_query_json_format(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["query", "AAPL price", "--format", "json"])
        assert result.exit_code == 0

    @patch("zion_terminal.cli._build_orchestrator")
    def test_query_invalid_format(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["query", "AAPL", "--format", "xml"])
        assert result.exit_code != 0
        assert "Invalid format" in result.output

    @patch("zion_terminal.cli._build_orchestrator")
    def test_quote_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["quote", "AAPL"])
        assert result.exit_code == 0

    @patch("zion_terminal.cli._build_orchestrator")
    def test_history_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["history", "AAPL", "--period", "6mo", "--interval", "1wk"])
        assert result.exit_code == 0
        # Verify the actual flags were passed
        mock_orchestrator._retrieval.fetch.assert_called_once()
        call_args = mock_orchestrator._retrieval.fetch.call_args[0][0]
        assert call_args[0]["period"] == "6mo"
        assert call_args[0]["interval"] == "1wk"

    @patch("zion_terminal.cli._build_orchestrator")
    def test_financials_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["financials", "AAPL", "--statement", "balance", "--quarterly"])
        assert result.exit_code == 0
        call_args = mock_orchestrator._retrieval.fetch.call_args[0][0]
        assert call_args[0]["statement_type"] == "balance"
        assert call_args[0]["quarterly"] is True

    @patch("zion_terminal.cli._build_orchestrator")
    @patch("zion_terminal.cli.get_settings")
    def test_filings_command(self, mock_settings, mock_build, runner, mock_orchestrator):
        mock_settings.return_value = MagicMock(edgar_identity="Test test@test.com")
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["filings", "AAPL", "--form", "10-K", "--limit", "5"])
        assert result.exit_code == 0
        call_args = mock_orchestrator._retrieval.fetch.call_args[0][0]
        assert call_args[0]["form"] == "10-K"
        assert call_args[0]["limit"] == 5

    @patch("zion_terminal.cli.get_settings")
    def test_filings_no_identity(self, mock_settings, runner):
        mock_settings.return_value = MagicMock(edgar_identity="")
        result = runner.invoke(main, ["filings", "AAPL"])
        assert result.exit_code != 0
        assert "EDGAR_IDENTITY" in result.output

    @patch("zion_terminal.cli.get_settings")
    def test_macro_no_key(self, mock_settings, runner):
        mock_settings.return_value = MagicMock(fred_api_key="")
        result = runner.invoke(main, ["macro", "GDP"])
        assert result.exit_code != 0
        assert "FRED_API_KEY" in result.output

    @patch("zion_terminal.cli._build_orchestrator")
    def test_info_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["info", "AAPL"])
        assert result.exit_code == 0

    @patch("zion_terminal.cli._build_orchestrator")
    def test_synthesis_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["synthesis"])
        assert result.exit_code == 0

    def test_all_subcommands_exist(self, runner):
        """Verify every documented subcommand is registered."""
        result = runner.invoke(main, ["--help"])
        for cmd in ["query", "quote", "history", "financials", "filings", "macro", "info", "synthesis"]:
            assert cmd in result.output, f"Command '{cmd}' not found in help output"
