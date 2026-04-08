"""Tests for the intent parser."""

from src.orchestrator.intent_parser import (
    IntentParser,
    ParsedIntent,
    extract_tickers,
    detect_macro_series,
)


class TestTickerExtraction:
    def test_single_ticker(self):
        assert extract_tickers("Get AAPL price") == ["AAPL"]

    def test_multiple_tickers(self):
        tickers = extract_tickers("Compare AAPL and NVDA")
        assert "AAPL" in tickers
        assert "NVDA" in tickers

    def test_no_tickers(self):
        assert extract_tickers("What is the GDP?") == []

    def test_filters_stop_words(self):
        # Common words like "AND", "THE" should be filtered
        tickers = extract_tickers("THE AAPL stock AND MSFT price")
        assert "THE" not in tickers
        assert "AND" not in tickers
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_possessive_ticker(self):
        tickers = extract_tickers("Pull NVDA's quarterly financials")
        assert "NVDA" in tickers


class TestMacroSeriesDetection:
    def test_fed_funds(self):
        series = detect_macro_series("What is the fed funds rate?")
        assert "FEDFUNDS" in series

    def test_gdp(self):
        series = detect_macro_series("Show me the latest GDP data")
        assert "GDP" in series

    def test_inflation(self):
        series = detect_macro_series("What is the current inflation rate?")
        assert "CPIAUCSL" in series

    def test_multiple(self):
        series = detect_macro_series("Compare GDP and unemployment rate")
        assert "GDP" in series
        assert "UNRATE" in series

    def test_treasury(self):
        series = detect_macro_series("Get the 10-year treasury yield")
        assert "DGS10" in series


class TestIntentParser:
    def setup_method(self):
        self.parser = IntentParser()

    def test_quote_intent(self):
        parsed = self.parser.parse("Get AAPL stock price")
        assert parsed.intent == "quote"
        assert "AAPL" in parsed.tickers
        assert len(parsed.tasks) >= 1
        assert parsed.needs_retrieval

    def test_history_intent(self):
        parsed = self.parser.parse("Show TSLA historical performance")
        assert parsed.intent == "history"
        assert "TSLA" in parsed.tickers

    def test_financials_intent(self):
        parsed = self.parser.parse("Pull NVDA's quarterly financials")
        assert parsed.intent == "financials"
        assert "NVDA" in parsed.tickers

    def test_filings_intent(self):
        parsed = self.parser.parse("List AAPL's SEC 10-K filings")
        assert parsed.intent == "filings"
        assert "AAPL" in parsed.tickers
        task = parsed.tasks[0]
        assert task["source"] == "sec_edgar"
        assert task["form"] == "10-K"

    def test_macro_intent(self):
        parsed = self.parser.parse("What is the current fed funds rate?")
        assert parsed.intent == "macro"
        assert "FEDFUNDS" in parsed.macro_series

    def test_multi_intent(self):
        parsed = self.parser.parse("Pull NVDA's quarterly financials and the current fed funds rate")
        assert parsed.intent == "multi"
        assert "NVDA" in parsed.tickers
        assert "FEDFUNDS" in parsed.macro_series
        assert len(parsed.tasks) >= 2

    def test_synthesis_intent(self):
        parsed = self.parser.parse("Generate a synthetic company")
        assert parsed.intent == "synthesis"
        assert parsed.needs_synthesis

    def test_quarterly_param(self):
        parsed = self.parser.parse("Get quarterly earnings for AAPL")
        assert parsed.params.get("quarterly") is True

    def test_balance_sheet_routing(self):
        parsed = self.parser.parse("Show MSFT balance sheet")
        assert parsed.intent == "financials"
        task = parsed.tasks[0]
        assert task["statement_type"] == "balance"

    def test_cash_flow_routing(self):
        parsed = self.parser.parse("Get GOOGL cash flow statement")
        assert parsed.intent == "financials"
        task = parsed.tasks[0]
        assert task["statement_type"] == "cash_flow"
