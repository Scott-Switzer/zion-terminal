"""Tests for the direct SEC client (sec/client.py).

All tests use mocked HTTP responses — no live SEC API calls.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from zion_terminal.sec.client import SECClient, _filing_in_quarter_by_report_date, _filing_year


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
                    "reportDate": [
                        "2024-09-28",
                        "2023-09-30",
                        "2022-09-24",
                        "2022-03-26",
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
        """year= filters on reportDate. reportDates have 2 in 2022, 1 in 2023, 1 in 2024."""
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL", year=2022)
            assert len(filings) == 2  # reportDate 2022-09-24 and 2022-03-26

    def test_get_filings_form_and_year(self, client, mock_submissions):
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL", form="10-K", year=2022)
            assert len(filings) == 1
            assert filings[0]["form"] == "10-K"
            assert filings[0]["reportDate"].startswith("2022")

    def test_get_filings_quarter_filter(self, client, mock_submissions):
        """quarter= filters on reportDate. reportDate 2022-03-26 is Q1."""
        with patch.object(client, "get_submissions", return_value=mock_submissions):
            filings = client.get_filings("AAPL", quarter=1)
            # reportDate 2022-03-26 is in Q1
            assert len(filings) == 1
            assert filings[0]["reportDate"] == "2022-03-26"

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
    """Tests for _filing_in_quarter_by_report_date and _filing_year.

    These use reportDate first, falling back to filingDate.
    """
    def test_q1(self):
        assert _filing_in_quarter_by_report_date({"reportDate": "2022-02-15"}, 1) is True
    def test_q2(self):
        assert _filing_in_quarter_by_report_date({"reportDate": "2022-05-06"}, 2) is True
    def test_q3(self):
        assert _filing_in_quarter_by_report_date({"reportDate": "2022-08-01"}, 3) is True
    def test_q4(self):
        assert _filing_in_quarter_by_report_date({"reportDate": "2022-11-03"}, 4) is True
    def test_wrong_quarter(self):
        assert _filing_in_quarter_by_report_date({"reportDate": "2022-11-03"}, 1) is False
    def test_bad_date(self):
        assert _filing_in_quarter_by_report_date({"reportDate": "bad"}, 1) is False
    def test_fallback_to_filing_date(self):
        """When reportDate is missing, should fall back to filingDate."""
        assert _filing_in_quarter_by_report_date({"filingDate": "2022-05-06"}, 2) is True
    def test_report_date_preferred_over_filing_date(self):
        """reportDate takes precedence when both present."""
        filing = {"reportDate": "2022-03-31", "filingDate": "2022-05-06"}
        assert _filing_in_quarter_by_report_date(filing, 1) is True  # Q1 from reportDate
        assert _filing_in_quarter_by_report_date(filing, 2) is False  # NOT Q2 from filingDate

    def test_filing_year_from_report_date(self):
        assert _filing_year({"reportDate": "2023-09-30", "filingDate": "2024-01-05"}) == 2023
    def test_filing_year_fallback_to_filing_date(self):
        assert _filing_year({"reportDate": "", "filingDate": "2024-01-05"}) == 2024
    def test_filing_year_no_report_date_key(self):
        assert _filing_year({"filingDate": "2024-01-05"}) == 2024
    def test_filing_year_bad_data(self):
        assert _filing_year({"reportDate": "bad"}) is None
