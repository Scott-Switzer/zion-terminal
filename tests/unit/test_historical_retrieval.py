"""Tests for historical retrieval features — year/quarter extraction, filtering, and fallback semantics."""

import pytest

from zion_terminal.orchestrator.intent_parser import (
    IntentParser, extract_year, extract_quarter,
)


class TestYearExtraction:
    """Test year extraction from NL queries."""

    def test_explicit_year(self):
        assert extract_year("Apple's 2022 10-K") == 2022

    def test_fy_prefix(self):
        assert extract_year("FY2021 annual report") == 2021

    def test_fiscal_year_phrase(self):
        assert extract_year("fiscal year 2023 balance sheet") == 2023

    def test_no_year(self):
        assert extract_year("Show me AAPL financials") is None

    def test_year_in_range(self):
        assert extract_year("2019 income statement") == 2019

    def test_does_not_match_non_year(self):
        # "10-K" should not be matched as a year
        assert extract_year("AAPL 10-K filing") is None


class TestQuarterExtraction:
    """Test quarter extraction from NL queries."""

    def test_q_notation(self):
        assert extract_quarter("Q2 2021 10-Q") == 2

    def test_quarter_word(self):
        assert extract_quarter("quarter 3 financials") == 3

    def test_lowercase_q(self):
        assert extract_quarter("q4 balance sheet") == 4

    def test_no_quarter(self):
        assert extract_quarter("Show me AAPL financials") is None


class TestNLHistoricalParsing:
    """Test that NL queries with years/quarters produce correct task params."""

    def setup_method(self):
        self.parser = IntentParser()

    def test_apple_2022_10k(self):
        parsed = self.parser.parse("Show me Apple's 2022 10-K")
        assert parsed.params.get("year") == 2022
        # Should have at least one SEC task
        sec_tasks = [t for t in parsed.tasks if t.get("source") == "sec_edgar"]
        assert sec_tasks
        assert sec_tasks[0].get("year") == 2022

    def test_msft_q2_2021(self):
        parsed = self.parser.parse("MSFT Q2 2021 10-Q")
        assert parsed.params.get("year") == 2021
        assert parsed.params.get("quarter") == 2
        sec_tasks = [t for t in parsed.tasks if t.get("source") == "sec_edgar"]
        assert sec_tasks
        assert sec_tasks[0].get("year") == 2021
        assert sec_tasks[0].get("quarter") == 2

    def test_nvda_5_years(self):
        parsed = self.parser.parse("NVDA last 5 10-K filings")
        sec_tasks = [t for t in parsed.tasks if t.get("source") == "sec_edgar"]
        assert sec_tasks
        assert sec_tasks[0].get("limit") == 5


class TestVerificationHonesty:
    """Test that verification status is honest about depth."""

    def test_structural_only_not_passed(self):
        from zion_terminal.pipeline.filing_pipeline import FilingPipeline
        pipeline = FilingPipeline()
        result = pipeline.process(
            html="<html><body>" + "<p>Content paragraph. " * 50 + "</p></body></html>",
            ticker="TEST",
            form="10-K",
        )
        # Without XBRL or cross-source, should NOT claim "passed"
        assert result.verification.get("status") == "structural_only"
        assert result.verification.get("verification_depth") == "structural_only"

    def test_verification_depth_field_exists(self):
        from zion_terminal.pipeline.filing_pipeline import FilingPipeline
        pipeline = FilingPipeline()
        result = pipeline.process(
            html="<html><body><p>Test</p></body></html>",
            ticker="TEST",
            form="10-K",
        )
        assert "verification_depth" in result.verification


class TestFallbackMetadata:
    """Test that fallback is tracked in RetrievalResult."""

    def test_retrieval_result_has_fallback_fields(self):
        from zion_terminal.models.responses import RetrievalResult
        r = RetrievalResult()
        assert hasattr(r, "fallback_used")
        assert hasattr(r, "actual_source")
        assert r.fallback_used is False
        assert r.actual_source == ""

    def test_retrieval_result_can_record_fallback(self):
        from zion_terminal.models.responses import RetrievalResult
        r = RetrievalResult(fallback_used=True, actual_source="yahoo_finance")
        assert r.fallback_used is True
        assert r.actual_source == "yahoo_finance"
