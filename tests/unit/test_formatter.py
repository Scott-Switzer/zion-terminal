"""Tests for output formatters – markdown tables, JSON schema, CSV."""

import csv
import io
import json

import pytest

from zion_terminal.models.responses import (
    OrchestratorResponse,
    RetrievalResult,
    SynthesisResult,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)
from zion_terminal.outputs.formatter import OutputFormat, format_response


def _make_quote_response() -> OrchestratorResponse:
    return OrchestratorResponse(
        success=True, query="AAPL stock price", intent="quote",
        results=[RetrievalResult(data=[{
            "ticker": "AAPL", "company_name": "Apple Inc.",
            "price": 185.50, "volume": 50_000_000,
            "market_cap": 2_900_000_000_000,
            "source": "yahoo_finance",
        }], sources_used=["yahoo_finance"])],
    )


def _make_timeseries_response() -> OrchestratorResponse:
    return OrchestratorResponse(
        success=True, query="AAPL history", intent="history",
        results=[RetrievalResult(data=[{
            "name": "AAPL Historical Prices", "ticker": "AAPL",
            "data_points": [
                {"date": "2024-01-01", "open": 180.0, "close": 182.5, "volume": 1000000},
                {"date": "2024-01-02", "open": 183.0, "close": 185.0, "volume": 1200000},
            ],
            "source": "yahoo_finance",
        }], sources_used=["yahoo_finance"])],
    )


def _make_error_response() -> OrchestratorResponse:
    return OrchestratorResponse(
        success=False, query="unknown", errors=["Something went wrong"],
    )


class TestMarkdownFormat:
    def test_quote_has_valid_table(self):
        md = format_response(_make_quote_response(), OutputFormat.MARKDOWN)
        lines = md.split("\n")
        table_lines = [l for l in lines if l.startswith("|")]
        # Should have header, separator, and at least one data row
        assert len(table_lines) >= 3
        # Check separator row has dashes
        assert any("---" in l for l in table_lines)

    def test_quote_contains_price(self):
        md = format_response(_make_quote_response(), OutputFormat.MARKDOWN)
        assert "185.50" in md or "185" in md

    def test_timeseries_has_table(self):
        md = format_response(_make_timeseries_response(), OutputFormat.MARKDOWN)
        assert "|" in md
        assert "2024-01-01" in md

    def test_error_shows_errors(self):
        md = format_response(_make_error_response(), OutputFormat.MARKDOWN)
        assert "Something went wrong" in md

    def test_validation_in_markdown(self):
        resp = OrchestratorResponse(
            success=True, query="test",
            results=[ValidationResult(
                status=ValidationStatus.PASSED,
                checks_run=2, checks_passed=2, checks_failed=0,
                details=[
                    ValidationCheck(check_name="test1", status=ValidationStatus.PASSED, message="ok"),
                    ValidationCheck(check_name="test2", status=ValidationStatus.PASSED, message="ok"),
                ],
            )],
        )
        md = format_response(resp, OutputFormat.MARKDOWN)
        assert "PASS" in md
        assert "test1" in md


class TestJSONFormat:
    def test_valid_json(self):
        output = format_response(_make_quote_response(), OutputFormat.JSON)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_stable_schema(self):
        output = format_response(_make_quote_response(), OutputFormat.JSON)
        parsed = json.loads(output)
        # Must have these top-level keys
        assert "success" in parsed
        assert "query" in parsed
        assert "intent" in parsed
        assert "errors" in parsed
        assert "data" in parsed
        assert "validation" in parsed
        assert "metadata" in parsed

    def test_data_contains_items(self):
        output = format_response(_make_quote_response(), OutputFormat.JSON)
        parsed = json.loads(output)
        assert len(parsed["data"]) > 0
        assert parsed["data"][0]["ticker"] == "AAPL"

    def test_error_response_json(self):
        output = format_response(_make_error_response(), OutputFormat.JSON)
        parsed = json.loads(output)
        assert parsed["success"] is False
        assert "Something went wrong" in parsed["errors"]


class TestCSVFormat:
    def test_valid_csv(self):
        output = format_response(_make_quote_response(), OutputFormat.CSV)
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)
        assert len(rows) > 0

    def test_csv_has_header(self):
        output = format_response(_make_quote_response(), OutputFormat.CSV)
        first_line = output.split("\n")[0]
        assert "ticker" in first_line

    def test_empty_response_csv(self):
        output = format_response(OrchestratorResponse(), OutputFormat.CSV)
        assert output == ""

    def test_timeseries_csv(self):
        output = format_response(_make_timeseries_response(), OutputFormat.CSV)
        assert len(output) > 0
