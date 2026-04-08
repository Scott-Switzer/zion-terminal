"""Tests for output formatters."""

import json

from src.models.responses import OrchestratorResponse, RetrievalResult, ValidationResult, ValidationStatus
from src.outputs.formatter import OutputFormatter, OutputFormat


class TestOutputFormatter:
    def setup_method(self):
        self.formatter = OutputFormatter()
        self.response = OrchestratorResponse(
            query="Get AAPL price",
            intent="quote",
            results=[
                RetrievalResult(
                    data=[{
                        "ticker": "AAPL",
                        "company_name": "Apple Inc",
                        "price": 185.50,
                        "volume": 45000000,
                        "source": "yahoo_finance",
                    }],
                    sources_used=["yahoo_finance"],
                ),
            ],
        )

    def test_markdown_output(self):
        output = self.formatter.format(self.response, OutputFormat.MARKDOWN)
        assert "Zion Terminal" in output
        assert "AAPL" in output or "Apple" in output
        assert "quote" in output

    def test_json_output(self):
        output = self.formatter.format(self.response, OutputFormat.JSON)
        parsed = json.loads(output)
        assert parsed["success"] is True
        assert parsed["query"] == "Get AAPL price"
        assert len(parsed["data"]) == 1

    def test_csv_output(self):
        output = self.formatter.format(self.response, OutputFormat.CSV)
        assert "ticker" in output
        assert "AAPL" in output

    def test_empty_response_csv(self):
        empty = OrchestratorResponse(query="Nothing", results=[])
        output = self.formatter.format(empty, OutputFormat.CSV)
        assert "No data" in output

    def test_markdown_with_validation(self):
        resp = OrchestratorResponse(
            query="Test",
            results=[
                RetrievalResult(data=[{"source": "test"}]),
                ValidationResult(
                    status=ValidationStatus.PASSED,
                    checks_run=3,
                    checks_passed=3,
                ),
            ],
        )
        output = self.formatter.format(resp, OutputFormat.MARKDOWN)
        assert "Validation" in output
        assert "passed" in output.lower()

    def test_markdown_macro_data(self):
        resp = OrchestratorResponse(
            query="Get GDP",
            results=[
                RetrievalResult(
                    data=[{
                        "series_id": "GDP",
                        "title": "Gross Domestic Product",
                        "value": 27_000_000,
                        "unit": "Billions of Dollars",
                        "frequency": "Quarterly",
                        "observation_date": "2024-07-01",
                        "source": "fred",
                    }],
                ),
            ],
        )
        output = self.formatter.format(resp, OutputFormat.MARKDOWN)
        assert "GDP" in output
        assert "Gross Domestic Product" in output
