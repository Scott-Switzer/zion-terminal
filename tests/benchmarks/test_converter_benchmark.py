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


class TestLargerFilingFixture:
    """Tests using the larger multi-section filing fixture."""

    @pytest.fixture
    def large_html(self):
        return (FIXTURES_DIR / "sample_html_filing_table_toc.html").read_text()

    def test_converts_successfully(self, converter, large_html):
        result = converter.convert(large_html)
        assert result["char_count"] > 500
        assert "Global Tech" in result["markdown"]

    def test_multiple_tables_preserved(self, converter, large_html):
        md = converter.convert(large_html)["markdown"]
        # Should have at least 4 tables (TOC, revenue, opex, balance sheet, exhibits)
        assert md.count("| ---") >= 4

    def test_segments_10_sections(self, converter, segmenter, large_html):
        md = converter.convert(large_html)["markdown"]
        sections = segmenter.segment(md)
        # Should find: 1, 1A, 2, 7, 7A, 8, 9, 9A, 15 = 9 sections
        assert len(sections) >= 8
        items = {s.item for s in sections}
        assert "Item 1" in items
        assert "Item 7" in items
        assert "Item 8" in items
        assert "Item 15" in items

    def test_section_content_not_empty(self, converter, segmenter, large_html):
        md = converter.convert(large_html)["markdown"]
        sections = segmenter.segment(md)
        for s in sections:
            assert not s.is_empty, f"Section {s.item} is empty"


class TestDivHeaderFixture:
    """Tests using the div-header filing fixture."""

    @pytest.fixture
    def div_html(self):
        return (FIXTURES_DIR / "sample_html_filing_div_headers.html").read_text()

    def test_div_headers_converted(self, converter, div_html):
        md = converter.convert(div_html)["markdown"]
        assert "## Item 1" in md
        assert "## Item 1A" in md

    def test_div_headers_segmented(self, converter, segmenter, div_html):
        md = converter.convert(div_html)["markdown"]
        sections = segmenter.segment(md)
        assert len(sections) >= 4
        items = {s.item for s in sections}
        assert "Item 1" in items
        assert "Item 7" in items


class TestLivePipelineBenchmarks:
    """Benchmarks for the unified filing pipeline (the actual live path)."""

    @pytest.fixture
    def pipeline(self):
        from zion_terminal.pipeline.filing_pipeline import FilingPipeline
        return FilingPipeline()

    def test_pipeline_process_small(self, benchmark, pipeline, sample_html):
        """Benchmark the full pipeline (convert + segment + verify hooks)."""
        benchmark(pipeline.process, sample_html, ticker="TEST", form="10-K")

    def test_pipeline_process_div_headers(self, benchmark, pipeline):
        """Benchmark pipeline on div-header fixture."""
        html = (FIXTURES_DIR / "sample_html_filing_div_headers.html").read_text()
        benchmark(pipeline.process, html, ticker="TEST", form="10-K")
