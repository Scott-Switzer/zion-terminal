"""Tests for the intent parser – ticker extraction, macro detection, parameter parsing."""

import pytest

from zion_terminal.orchestrator.intent_parser import (
    IntentParser,
    ParsedIntent,
    detect_macro_series,
    extract_interval,
    extract_limit,
    extract_period,
    extract_tickers,
)


class TestExtractTickers:
    def test_uppercase_ticker(self):
        assert extract_tickers("Get AAPL stock price") == ["AAPL"]

    def test_multiple_tickers(self):
        result = extract_tickers("Compare AAPL and MSFT")
        assert "AAPL" in result
        assert "MSFT" in result

    def test_lowercase_known_ticker(self):
        result = extract_tickers("get aapl price")
        assert "AAPL" in result

    def test_company_name(self):
        result = extract_tickers("Show me Apple financials")
        assert "AAPL" in result

    def test_company_name_case_insensitive(self):
        result = extract_tickers("what is tesla worth")
        assert "TSLA" in result

    def test_stop_words_excluded(self):
        result = extract_tickers("GET THE PRICE OF AAPL")
        assert "AAPL" in result
        assert "GET" not in result
        assert "THE" not in result

    def test_no_ticker_found(self):
        result = extract_tickers("what is the weather")
        assert result == []

    def test_multiword_company_name(self):
        result = extract_tickers("Goldman Sachs earnings")
        assert "GS" in result


class TestDetectMacroSeries:
    def test_gdp(self):
        assert "GDP" in detect_macro_series("What is the GDP?")

    def test_inflation(self):
        assert "CPIAUCSL" in detect_macro_series("Show me the inflation rate")

    def test_unemployment(self):
        assert "UNRATE" in detect_macro_series("What is unemployment?")

    def test_treasury(self):
        assert "DGS10" in detect_macro_series("10-year treasury yield")

    def test_no_macro(self):
        assert detect_macro_series("AAPL stock price") == []

    def test_multiple_series(self):
        result = detect_macro_series("Compare GDP and unemployment")
        assert "GDP" in result
        assert "UNRATE" in result


class TestExtractPeriod:
    def test_6mo(self):
        assert extract_period("last 6 months") == "6mo"

    def test_1y(self):
        assert extract_period("past 1 year") == "1y"

    def test_ytd(self):
        assert extract_period("ytd performance") == "ytd"

    def test_max(self):
        assert extract_period("max history") == "max"

    def test_none(self):
        assert extract_period("AAPL price") is None

    def test_days(self):
        assert extract_period("last 30 days") == "30d"

    def test_weeks(self):
        assert extract_period("past 2 weeks") == "2wk"


class TestExtractInterval:
    def test_daily(self):
        assert extract_interval("daily prices") == "1d"

    def test_weekly(self):
        assert extract_interval("weekly chart") == "1wk"

    def test_monthly(self):
        assert extract_interval("monthly data") == "1mo"

    def test_1wk_literal(self):
        assert extract_interval("interval 1wk") == "1wk"

    def test_none(self):
        assert extract_interval("AAPL price") is None


class TestExtractLimit:
    def test_last_n(self):
        assert extract_limit("last 5 filings") == 5

    def test_top_n(self):
        assert extract_limit("top 10 results") == 10

    def test_limit_n(self):
        assert extract_limit("limit 20") == 20

    def test_none(self):
        assert extract_limit("AAPL price") is None


class TestIntentParser:
    def setup_method(self):
        self.parser = IntentParser()  # no LLM

    def test_quote_intent(self):
        parsed = self.parser.parse("Get AAPL stock price")
        assert parsed.intent == "quote"
        assert "AAPL" in parsed.tickers
        assert len(parsed.tasks) > 0
        assert parsed.tasks[0]["action"] == "quote"

    def test_history_intent(self):
        parsed = self.parser.parse("AAPL historical prices last 6 months weekly")
        assert parsed.intent == "history"
        assert "AAPL" in parsed.tickers
        assert parsed.tasks[0]["action"] == "history"
        assert parsed.tasks[0]["period"] == "6mo"
        assert parsed.tasks[0]["interval"] == "1wk"

    def test_filings_intent(self):
        parsed = self.parser.parse("AAPL 10-K filings last 5")
        assert parsed.intent == "filings"
        assert parsed.tasks[0]["action"] == "filings"
        assert parsed.tasks[0]["form"] == "10-K"
        assert parsed.tasks[0]["limit"] == 5

    def test_financials_intent(self):
        parsed = self.parser.parse("AAPL quarterly income statement")
        assert parsed.intent == "financials"
        assert parsed.tasks[0]["quarterly"] is True

    def test_macro_intent(self):
        parsed = self.parser.parse("Show me the unemployment rate")
        assert parsed.intent == "macro"
        assert "UNRATE" in parsed.macro_series
        assert len(parsed.tasks) > 0
        assert parsed.tasks[0]["source"] == "fred"

    def test_multi_intent(self):
        parsed = self.parser.parse("Compare AAPL stock with GDP")
        assert parsed.intent == "multi"
        assert "AAPL" in parsed.tickers
        assert "GDP" in parsed.macro_series

    def test_synthesis_intent(self):
        parsed = self.parser.parse("Generate a synthetic company")
        assert parsed.intent == "synthesis"
        assert parsed.needs_synthesis is True

    def test_empty_query_no_tasks(self):
        parsed = self.parser.parse("hello world")
        assert parsed.tasks == []

    def test_company_name_in_query(self):
        parsed = self.parser.parse("What is Apple's revenue?")
        assert "AAPL" in parsed.tickers

    def test_params_passed_through(self):
        parsed = self.parser.parse("AAPL history last 3 months weekly limit 10")
        assert parsed.params.get("period") == "3mo"
        assert parsed.params.get("interval") == "1wk"
        assert parsed.params.get("limit") == 10
