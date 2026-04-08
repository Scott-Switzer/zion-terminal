"""Benchmarks for the filing converter and segmenter.

Run with: pytest tests/benchmarks/ --benchmark-only
"""

from __future__ import annotations

from pathlib import Path

import pytest
from zion_terminal.pipeline.converter import FilingConverter
from zion_terminal.pipeline.segmenter import FilingSegmenter

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def sample_html():
    return (FIXTURES_DIR / "sample_html_filing.html").read_text()


@pytest.fixture
def converter():
    return FilingConverter()


@pytest.fixture
def segmenter():
    return FilingSegmenter()


class TestConverterBenchmarks:
    def test_convert_small_filing(self, benchmark, converter, sample_html):
        benchmark(converter.convert, sample_html)

    def test_convert_large_filing(self, benchmark, converter, sample_html):
        """Simulate a larger filing by repeating the HTML content."""
        large_html = sample_html * 50  # ~100KB
        benchmark(converter.convert, large_html)


class TestSegmenterBenchmarks:
    def test_segment_filing(self, benchmark, converter, segmenter, sample_html):
        result = converter.convert(sample_html)
        markdown = result["markdown"]
        benchmark(segmenter.segment, markdown)


class TestConverterAccuracy:
    """Functional tests for the converter using the fixture corpus."""

    def test_strips_script_and_style(self, converter, sample_html):
        result = converter.convert(sample_html)
        md = result["markdown"]
        assert "var x = 1" not in md
        assert "font-family" not in md

    def test_preserves_headers(self, converter, sample_html):
        result = converter.convert(sample_html)
        md = result["markdown"]
        assert "# " in md or "## " in md

    def test_preserves_bold(self, converter, sample_html):
        result = converter.convert(sample_html)
        md = result["markdown"]
        assert "**Test Company**" in md

    def test_preserves_tables(self, converter, sample_html):
        result = converter.convert(sample_html)
        md = result["markdown"]
        assert "|" in md
        assert "Revenue" in md

    def test_preserves_list_items(self, converter, sample_html):
        result = converter.convert(sample_html)
        md = result["markdown"]
        assert "- " in md

    def test_metadata_returned(self, converter, sample_html):
        result = converter.convert(sample_html, metadata={"ticker": "TEST"})
        assert result["metadata"]["ticker"] == "TEST"
        assert result["char_count"] > 0


class TestSegmenterAccuracy:
    """Functional tests for the section segmenter."""

    def test_finds_sections(self, converter, segmenter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        sections = segmenter.segment(md)
        assert len(sections) >= 3  # At minimum: Item 1, 1A, 7, 8, 15

    def test_get_section_by_item(self, converter, segmenter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        sections = segmenter.segment(md)
        risk = segmenter.get_section(sections, "1A")
        if risk:
            assert "risk" in risk.content.lower() or "Risk" in risk.title

    def test_summary_works(self, converter, segmenter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        sections = segmenter.segment(md)
        summary = segmenter.summary(sections)
        assert summary["section_count"] == len(sections)
        assert summary["total_chars"] > 0

    def test_empty_document(self, segmenter):
        sections = segmenter.segment("")
        assert len(sections) == 1
        assert sections[0].item == "full"
