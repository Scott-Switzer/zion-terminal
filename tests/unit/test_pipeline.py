"""Tests for the filing conversion pipeline."""

from pathlib import Path

import pytest

from zion_terminal.pipeline.converter import FilingConverter
from zion_terminal.pipeline.segmenter import FilingSegmenter, FilingSection
from zion_terminal.pipeline.xbrl import XBRLVerifier, is_available

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


class TestFilingConverter:
    def test_basic_conversion(self, converter, sample_html):
        result = converter.convert(sample_html)
        assert "markdown" in result
        assert result["char_count"] > 0
        assert "UNITED STATES" in result["markdown"]

    def test_strips_script_tags(self, converter, sample_html):
        result = converter.convert(sample_html)
        assert "var x = 1" not in result["markdown"]

    def test_strips_style_tags(self, converter, sample_html):
        result = converter.convert(sample_html)
        assert "font-family" not in result["markdown"]

    def test_converts_headers(self, converter, sample_html):
        result = converter.convert(sample_html)
        assert "## " in result["markdown"] or "# " in result["markdown"]

    def test_converts_tables(self, converter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        assert "|" in md
        assert "Revenue" in md
        assert "---" in md

    def test_preserves_list_items(self, converter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        assert "- " in md

    def test_metadata_passthrough(self, converter, sample_html):
        result = converter.convert(sample_html, metadata={"ticker": "TEST"})
        assert result["metadata"]["ticker"] == "TEST"

    def test_truncation(self, sample_html):
        c = FilingConverter(max_length=100)
        result = c.convert(sample_html)
        assert result["metadata"]["truncated"]
        assert "[... truncated ...]" in result["markdown"]

    def test_empty_html(self, converter):
        result = converter.convert("")
        assert result["char_count"] == 0 or result["markdown"] == ""


class TestFilingSegmenter:
    def test_finds_items(self, converter, segmenter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        sections = segmenter.segment(md)
        assert len(sections) >= 3

    def test_section_has_content(self, converter, segmenter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        sections = segmenter.segment(md)
        for s in sections:
            assert isinstance(s, FilingSection)
            assert s.item.startswith("Item") or s.item == "full"

    def test_get_section(self, converter, segmenter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        sections = segmenter.segment(md)
        s = segmenter.get_section(sections, "1")
        assert s is not None
        assert "Business" in s.title or "business" in s.content.lower()

    def test_get_section_missing(self, segmenter):
        sections = [FilingSection(item="Item 1", title="Business", content="test")]
        assert segmenter.get_section(sections, "99") is None

    def test_summary(self, converter, segmenter, sample_html):
        md = converter.convert(sample_html)["markdown"]
        sections = segmenter.segment(md)
        summary = segmenter.summary(sections)
        assert summary["section_count"] == len(sections)
        assert summary["total_chars"] > 0

    def test_empty_document(self, segmenter):
        sections = segmenter.segment("")
        assert len(sections) == 1
        assert sections[0].item == "full"

    def test_no_items_returns_full(self, segmenter):
        sections = segmenter.segment("Just some text without any items.")
        assert len(sections) == 1
        assert sections[0].item == "full"


class TestXBRLVerifier:
    def test_availability_check(self):
        # is_available returns bool regardless of install status
        result = is_available()
        assert isinstance(result, bool)

    def test_verifier_without_arelle(self):
        v = XBRLVerifier()
        if not v.is_available:
            result = v.validate_url("https://example.com/test.xml")
            assert not result.valid
            assert "not installed" in result.errors[0].lower()
