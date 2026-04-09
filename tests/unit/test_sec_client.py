"""Tests for the direct SEC client (sec/client.py).

All tests use mocked HTTP responses — no live SEC API calls.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from zion_terminal.sec.client import SECClient, _filing_in_quarter


class TestTickerResolution:
    """Test ticker→CIK resolution."""

    def setup_method(self):
        # Reset the module-level ticker map to avoid cross-test pollution
        import zion_terminal.sec.client as mod
        mod._ticker_map = None

    def test_resolve_cik_basic(self):
        client = SECClient(identity="Test test@test.com")
        mock_data = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        }
        with patch.object(client, "_load_ticker_map", return_value={
            "AAPL": mock_data["0"],
            "MSFT": mock_data["1"],
        }):
            assert client.resolve_cik("AAPL") == "0000320193"
            assert client.resolve_cik("MSFT") == "0000789019"
            assert client.resolve_cik("ZZZZ") is None

    def test_resolve_cik_handles_dot_tickers(self):
        """BRK.B should resolve same as BRK-B."""
        client = SECClient(identity="Test test@test.com")
        import zion_terminal.sec.client as mod
        mod._ticker_map = {
            "BRK-B": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway"},
        }
        assert client.resolve_cik("BRK.B") == "0001067983"
        mod._ticker_map = None  # cleanup

    def test_cik_zero_padding(self):
        client = SECClient(identity="Test test@test.com")
        import zion_terminal.sec.client as mod
        mod._ticker_map = {
            "X": {"cik_str": 123, "ticker": "X", "title": "US Steel"},
        }
        assert client.resolve_cik("X") == "0000000123"
        mod._ticker_map = None  # cleanup


class TestFilingDiscovery:
    """Test filing list retrieval with filters."""

    @pytest.fixture
    def client(self):
        c = SECClient(identity="Test test@test.com")
        # Pre-populate ticker map
        import zion_terminal.sec.client as mod
        mod._ticker_map = {
            "AAPL": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        }
        return c

    @pytest.fixture
    def mock_submissions(self):
        return {
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "accessionNumber": [
                        "0000320193-24-000123",
                        "0000320193-23-000100",
                        "0000320193-22-000050",
                        "0000320193-22-000030",
                    ],
                    "filingDate": [
                        "2024-11-01",
                        "2023-11-03",
                        "2022-10-28",
                        "2022-05-06",
                    ],
                    "form": ["10-K", "10-K", "10-K", "10-Q"],
                    "primaryDocument": [
                        "aapl-20240928.htm",
                        "aapl-20230930.htm",
                        "aapl-20220924.htm",
                        "aapl-20220326.htm",
                    ],
                    "primaryDocDescription": [
                        "10-K", "10-K", "10-K", "10-Q",
                    ],
                },
            },
        }

    def test_get_filings_no_filter(self, client, mock_submissions):
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL")
            assert len(filings) == 4

    def test_get_filings_form_filter(self, client, mock_submissions):
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL", form="10-K")
            assert len(filings) == 3
            assert all(f["form"] == "10-K" for f in filings)

    def test_get_filings_year_filter(self, client, mock_submissions):
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL", year=2022)
            assert len(filings) == 2  # Two filings in 2022

    def test_get_filings_form_and_year(self, client, mock_submissions):
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL", form="10-K", year=2022)
            assert len(filings) == 1
            assert filings[0]["form"] == "10-K"
            assert filings[0]["filingDate"].startswith("2022")

    def test_get_filings_quarter_filter(self, client, mock_submissions):
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL", quarter=2)
            # Only the 2022-05-06 filing is in Q2
            assert len(filings) == 1
            assert filings[0]["filingDate"] == "2022-05-06"

    def test_get_filings_limit(self, client, mock_submissions):
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL", limit=2)
            assert len(filings) == 2

    def test_get_filings_unknown_ticker(self, client):
        import zion_terminal.sec.client as mod
        mod._ticker_map = {}
        filings = client.get_filings("ZZZZ")
        assert filings == []


class TestFilingURL:
    def test_build_filing_url(self):
        client = SECClient()
        url = client.build_filing_url("0000320193", "0000320193-24-000123", "aapl-20240928.htm")
        assert "Archives/edgar/data/320193/" in url
        assert "000032019324000123" in url
        assert "aapl-20240928.htm" in url


class TestQuarterHelper:
    def test_q1(self):
        assert _filing_in_quarter("2022-02-15", 1) is True
    def test_q2(self):
        assert _filing_in_quarter("2022-05-06", 2) is True
    def test_q3(self):
        assert _filing_in_quarter("2022-08-01", 3) is True
    def test_q4(self):
        assert _filing_in_quarter("2022-11-03", 4) is True
    def test_wrong_quarter(self):
        assert _filing_in_quarter("2022-11-03", 1) is False
    def test_bad_date(self):
        assert _filing_in_quarter("bad", 1) is False
