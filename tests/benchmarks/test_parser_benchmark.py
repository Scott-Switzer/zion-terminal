"""Benchmarks for the intent parser.

Run with: pytest tests/benchmarks/ --benchmark-only
Requires: pip install pytest-benchmark
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from zion_terminal.orchestrator.intent_parser import (
    IntentParser,
    extract_tickers,
    detect_macro_series,
    extract_period,
    extract_interval,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def parser():
    return IntentParser()


@pytest.fixture
def sample_queries():
    with open(FIXTURES_DIR / "sample_queries.json") as f:
        return json.load(f)


class TestParserBenchmarks:
    """Benchmarks to prevent parser regressions and track performance."""

    def test_parse_single_query(self, benchmark, parser):
        benchmark(parser.parse, "Get AAPL stock price")

    def test_parse_corpus(self, benchmark, parser, sample_queries):
        """Parse the entire test corpus."""

        def run():
            for q in sample_queries:
                parser.parse(q["query"])

        benchmark(run)

    def test_extract_tickers(self, benchmark):
        benchmark(extract_tickers, "Show me AAPL, MSFT, and GOOGL prices for the last quarter")

    def test_detect_macro_series(self, benchmark):
        benchmark(detect_macro_series, "What are the current GDP, CPI, and unemployment rate?")

    def test_extract_period(self, benchmark):
        benchmark(extract_period, "Get 6 month historical data for TSLA")

    def test_extract_interval(self, benchmark):
        benchmark(extract_interval, "Show me AAPL weekly chart")


class TestParserCorpusAccuracy:
    """Validate the parser against the fixture corpus.

    This is not a benchmark per se, but it uses the fixture corpus
    to track accuracy regressions.
    """

    def test_intent_accuracy(self, parser, sample_queries):
        correct = 0
        total = 0
        failures: list[str] = []

        for q in sample_queries:
            query = q["query"]
            expected = q["expected_intent"]
            result = parser.parse(query)
            total += 1
            if result.intent == expected:
                correct += 1
            else:
                failures.append(f"  {query!r}: expected={expected}, got={result.intent}")

        accuracy = correct / total if total else 0
        if failures:
            detail = "\n".join(failures)
            pytest.fail(
                f"Intent accuracy: {accuracy:.0%} ({correct}/{total})\n"
                f"Failures:\n{detail}"
            )

    def test_ticker_extraction(self, parser, sample_queries):
        correct = 0
        total = 0
        failures: list[str] = []

        for q in sample_queries:
            expected_tickers = q.get("expected_tickers", [])
            if not expected_tickers:
                continue
            result = parser.parse(q["query"])
            total += 1
            if set(result.tickers) == set(expected_tickers):
                correct += 1
            else:
                failures.append(
                    f"  {q['query']!r}: expected={expected_tickers}, got={result.tickers}"
                )

        accuracy = correct / total if total else 0
        # Allow some tolerance — parser doesn't need 100% ticker accuracy
        assert accuracy >= 0.8, (
            f"Ticker extraction accuracy too low: {accuracy:.0%} ({correct}/{total})\n"
            + "\n".join(failures)
        )

    def test_macro_series_detection(self, parser, sample_queries):
        for q in sample_queries:
            expected_series = q.get("expected_macro_series", [])
            if not expected_series:
                continue
            result = parser.parse(q["query"])
            for sid in expected_series:
                assert sid in result.macro_series, (
                    f"Query {q['query']!r}: expected {sid} in macro_series, "
                    f"got {result.macro_series}"
                )
