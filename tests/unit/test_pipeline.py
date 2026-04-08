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

    def test_converter_metadata_reports_dom(self, converter, sample_html):
        """Converter metadata should report 'dom' when bs4 is available."""
        result = converter.convert(sample_html)
        assert result["metadata"]["converter"] == "dom"

    def test_converter_metadata_reports_regex_without_bs4(self, sample_html):
        """Converter metadata should report 'regex' when bs4 is not available."""
        import unittest.mock as mock
        converter = FilingConverter()
        with mock.patch.dict("sys.modules", {"bs4": None}):
            result = converter.convert(sample_html)
            assert result["metadata"]["converter"] == "regex"


class TestConverterItemPromotion:
    """Tests for converting real-filing HTML where Item headers are in div/p/b tags."""

    @pytest.fixture
    def div_html(self):
        return (FIXTURES_DIR / "sample_html_filing_div_headers.html").read_text()

    def test_item_in_p_bold_promoted(self, converter, div_html):
        md = converter.convert(div_html)["markdown"]
        # Item headers from <p><b>...</b></p> should be promoted to ## headings
        assert "## Item 1." in md or "## Item 1 " in md

    def test_item_in_strong_promoted(self, converter, div_html):
        md = converter.convert(div_html)["markdown"]
        assert "## Item 1A." in md or "## Item 1A " in md

    def test_item_in_div_promoted(self, converter, div_html):
        md = converter.convert(div_html)["markdown"]
        assert "## Item 7." in md or "## Item 7 " in md

    def test_segmenter_finds_promoted_items(self, converter, div_html):
        md = converter.convert(div_html)["markdown"]
        segmenter = FilingSegmenter()
        sections = segmenter.segment(md)
        item_nums = {s.item for s in sections}
        assert "Item 1" in item_nums
        assert "Item 1A" in item_nums
        assert "Item 7" in item_nums


class TestSegmenterPlainTextFallback:
    """Tests for the segmenter's fallback to plain-text Item headers."""

    def test_plain_item_headers_detected(self):
        md = """Some preamble text.

Item 1. Business
We are a technology company.

Item 1A. Risk Factors
Investing involves risk.

Item 7. Management's Discussion and Analysis
Revenue grew 10%.
"""
        segmenter = FilingSegmenter()
        sections = segmenter.segment(md)
        assert len(sections) >= 3
        item_nums = {s.item for s in sections}
        assert "Item 1" in item_nums
        assert "Item 1A" in item_nums
        assert "Item 7" in item_nums

    def test_heading_pattern_preferred_over_plain(self):
        """If headings exist, plain-text fallback should not be used."""
        md = """## Item 1. Business
We are a company.

## Item 1A. Risk Factors
Risk exists.

Item 7. This is a plain reference, not a header.
"""
        segmenter = FilingSegmenter()
        sections = segmenter.segment(md)
        item_nums = {s.item for s in sections}
        assert "Item 1" in item_nums
        assert "Item 1A" in item_nums
        # Item 7 as plain text should NOT be picked up since heading pattern found items
        assert "Item 7" not in item_nums


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

    def test_extract_concept_name_with_qname(self):
        """Positive path: concept has .qname.localName like real Arelle."""
        from unittest.mock import MagicMock
        fact = MagicMock()
        fact.concept.qname.localName = "Revenue"
        name = XBRLVerifier._extract_concept_name(fact)
        assert name == "Revenue"

    def test_extract_concept_name_no_concept(self):
        """Fact with no concept returns empty string."""
        from unittest.mock import MagicMock
        fact = MagicMock(spec=[])
        name = XBRLVerifier._extract_concept_name(fact)
        assert name == ""

    def test_extract_concept_name_fallback_to_name(self):
        """If concept has no qname but has .name, use that."""
        from unittest.mock import MagicMock
        fact = MagicMock()
        fact.concept.qname = None
        fact.concept.name = "Assets"
        name = XBRLVerifier._extract_concept_name(fact)
        assert name == "Assets"

    def test_extract_period_info_start_end(self):
        """Positive path: context is a start/end period."""
        from unittest.mock import MagicMock
        from datetime import datetime
        from zion_terminal.pipeline.xbrl import XBRLFact
        fact = MagicMock()
        fact.context.isStartEndPeriod = True
        fact.context.isInstantPeriod = False
        fact.context.startDatetime = datetime(2024, 1, 1)
        fact.context.endDatetime = datetime(2024, 12, 31)
        xf = XBRLFact(concept="Revenue", value="1000000")
        XBRLVerifier._extract_period_info(fact, xf)
        assert "2024" in xf.period_start
        assert "2024" in xf.period_end
        assert xf.period_instant is None

    def test_extract_period_info_instant(self):
        """Positive path: context is an instant period."""
        from unittest.mock import MagicMock
        from datetime import datetime
        from zion_terminal.pipeline.xbrl import XBRLFact
        fact = MagicMock()
        fact.context.isStartEndPeriod = False
        fact.context.isInstantPeriod = True
        fact.context.instantDatetime = datetime(2024, 12, 31)
        xf = XBRLFact(concept="Assets", value="5000000")
        XBRLVerifier._extract_period_info(fact, xf)
        assert xf.period_instant is not None
        assert "2024" in xf.period_instant
        assert xf.period_start is None

    def test_extract_period_info_no_context(self):
        """No context means no period info extracted."""
        from unittest.mock import MagicMock
        from zion_terminal.pipeline.xbrl import XBRLFact
        fact = MagicMock(spec=[])
        xf = XBRLFact(concept="Test", value="123")
        XBRLVerifier._extract_period_info(fact, xf)
        assert xf.period_start is None
        assert xf.period_end is None
        assert xf.period_instant is None
