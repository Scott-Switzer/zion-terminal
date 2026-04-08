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
    """Patch _build_orchestrator to return a mock.

    All public methods return a valid OrchestratorResponse so the
    CLI can format output without errors.
    """
    mock_orc = MagicMock()
    default_resp = OrchestratorResponse(
        success=True, query="test", intent="quote",
        results=[RetrievalResult(data=[{
            "ticker": "AAPL", "price": 185.50, "source": "yahoo_finance",
        }], sources_used=["yahoo_finance"])],
    )
    mock_orc.query.return_value = default_resp
    mock_orc.get_quote.return_value = default_resp
    mock_orc.get_history.return_value = default_resp
    mock_orc.get_financials.return_value = default_resp
    mock_orc.get_filings.return_value = default_resp
    mock_orc.get_macro.return_value = default_resp
    mock_orc.get_info.return_value = default_resp
    mock_orc.get_filing_markdown.return_value = default_resp
    mock_orc.get_company_facts.return_value = default_resp
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
        assert "0.4.0" in result.output

    def test_no_subcommand_shows_help(self, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "Usage" in result.output or "Commands" in result.output

    @patch("zion_terminal.cli._build_orchestrator")
    def test_query_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["query", "Get AAPL stock price"])
        assert result.exit_code == 0
        mock_orchestrator.query.assert_called_once()

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
        mock_orchestrator.get_quote.assert_called_once_with("AAPL")

    @patch("zion_terminal.cli._build_orchestrator")
    def test_history_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["history", "AAPL", "--period", "6mo", "--interval", "1wk"])
        assert result.exit_code == 0
        mock_orchestrator.get_history.assert_called_once_with(
            "AAPL", period="6mo", interval="1wk",
        )

    @patch("zion_terminal.cli._build_orchestrator")
    def test_financials_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["financials", "AAPL", "--statement", "balance", "--quarterly"])
        assert result.exit_code == 0
        mock_orchestrator.get_financials.assert_called_once_with(
            "AAPL", statement_type="balance", quarterly=True, source="sec",
        )

    @patch("zion_terminal.cli._build_orchestrator")
    @patch("zion_terminal.cli.get_settings")
    def test_filings_command(self, mock_settings, mock_build, runner, mock_orchestrator):
        mock_settings.return_value = MagicMock(edgar_identity="Test test@test.com")
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["filings", "AAPL", "--form", "10-K", "--limit", "5"])
        assert result.exit_code == 0
        mock_orchestrator.get_filings.assert_called_once_with(
            "AAPL", form="10-K", limit=5,
        )

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
    @patch("zion_terminal.cli.get_settings")
    def test_macro_command_with_dates(self, mock_settings, mock_build, runner, mock_orchestrator):
        mock_settings.return_value = MagicMock(fred_api_key="test-key")
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["macro", "GDP", "--start", "2020-01-01", "--end", "2024-12-31"])
        assert result.exit_code == 0
        mock_orchestrator.get_macro.assert_called_once_with(
            "GDP", start_date="2020-01-01", end_date="2024-12-31",
        )

    @patch("zion_terminal.cli._build_orchestrator")
    def test_info_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["info", "AAPL"])
        assert result.exit_code == 0
        mock_orchestrator.get_info.assert_called_once_with("AAPL")

    @patch("zion_terminal.cli._build_orchestrator")
    @patch("zion_terminal.cli.get_settings")
    def test_filing_markdown_command(self, mock_settings, mock_build, runner, mock_orchestrator):
        mock_settings.return_value = MagicMock(edgar_identity="Test test@test.com")
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["filing-markdown", "AAPL", "--form", "10-Q"])
        assert result.exit_code == 0
        mock_orchestrator.get_filing_markdown.assert_called_once_with(
            "AAPL", form="10-Q",
        )

    @patch("zion_terminal.cli._build_orchestrator")
    @patch("zion_terminal.cli.get_settings")
    def test_company_facts_command(self, mock_settings, mock_build, runner, mock_orchestrator):
        mock_settings.return_value = MagicMock(edgar_identity="Test test@test.com")
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["company-facts", "AAPL"])
        assert result.exit_code == 0
        mock_orchestrator.get_company_facts.assert_called_once_with("AAPL")

    @patch("zion_terminal.cli._build_orchestrator")
    def test_synthesis_command(self, mock_build, runner, mock_orchestrator):
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["synthesis"])
        assert result.exit_code == 0

    @patch("zion_terminal.cli._build_orchestrator")
    def test_synthesis_passes_strict(self, mock_build, runner, mock_orchestrator):
        """Verify --strict flag is forwarded to orchestrator from synthesis command."""
        mock_build.return_value = mock_orchestrator
        result = runner.invoke(main, ["--strict", "synthesis"])
        assert result.exit_code == 0
        mock_build.assert_called_once_with(strict=True)

    def test_all_subcommands_exist(self, runner):
        """Verify every documented subcommand is registered."""
        result = runner.invoke(main, ["--help"])
        for cmd in ["query", "quote", "history", "financials", "filings",
                     "macro", "info", "filing-markdown", "company-facts", "synthesis"]:
            assert cmd in result.output, f"Command '{cmd}' not found in help output"

    def test_strict_flag(self, runner):
        """Verify --strict is accepted as a global option."""
        result = runner.invoke(main, ["--strict", "--help"])
        assert result.exit_code == 0

    @patch("zion_terminal.cli._build_orchestrator")
    def test_cli_does_not_access_private_agents(self, mock_build, runner, mock_orchestrator):
        """Verify CLI commands use public orchestrator methods, never _retrieval.fetch."""
        mock_build.return_value = mock_orchestrator
        # Run several commands
        runner.invoke(main, ["quote", "AAPL"])
        runner.invoke(main, ["info", "TSLA"])
        # _retrieval.fetch should never be called directly
        mock_orchestrator._retrieval.fetch.assert_not_called()
