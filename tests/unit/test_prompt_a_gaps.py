"""Tests for remaining Prompt A gaps: schema alignment, TOC filtering, regeneration."""
import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestSECFinancialsSchema:
    """H4: SEC financials output must match formatter expectations."""

    def test_sec_financials_output_has_line_items(self):
        """SEC adapter financials output must include 'line_items' for formatter."""
        # The formatter detects financial statements by checking for 'line_items'
        from zion_terminal.outputs.formatter import _format_statement_md
        # This is the shape the SEC adapter should produce:
        sec_output = {
            "ticker": "AAPL",
            "statement_type": "income_statement",
            "period": "2024-12-31",
            "line_items": {"Total Revenue": 394328000000, "Net Income": 93736000000},
            "source": "sec_edgar",
        }
        md = _format_statement_md(sec_output)
        assert "AAPL" in md
        assert "Total Revenue" in md

    def test_sec_financials_detected_by_formatter(self):
        """Formatter must detect SEC financials by 'line_items' key."""
        from zion_terminal.outputs.formatter import format_response, OutputFormat
        from zion_terminal.models.responses import OrchestratorResponse, RetrievalResult
        resp = OrchestratorResponse(
            success=True, query="AAPL financials", intent="financials",
            results=[RetrievalResult(data=[{
                "ticker": "AAPL",
                "statement_type": "income_statement",
                "period": "2024-12-31",
                "line_items": {"Total Revenue": 394328000000},
                "source": "sec_edgar",
            }], sources_used=["sec_edgar"])],
        )
        output = format_response(resp, OutputFormat.MARKDOWN)
        assert "Income Statement" in output or "income" in output.lower()
        assert "Total Revenue" in output


class TestTOCFiltering:
    """A6: TOC entries should not create spurious section boundaries."""

    def test_toc_cluster_filtered(self):
        """Dense item cluster in first 20% should be filtered as TOC."""
        from zion_terminal.pipeline.segmenter import FilingSegmenter
        # Simulate a document where TOC lists items 1-8 in first 20 lines,
        # then real sections appear later.
        # Must be 200+ lines for the filter to activate.
        lines = []
        # TOC region (lines 0-15)
        lines.append("TABLE OF CONTENTS")
        lines.append("")
        for item in ["1", "1A", "2", "7", "8", "15"]:
            lines.append(f"## Item {item}. Page {item}")
        lines.extend([""] * 200)  # spacer (makes total > 200 lines)
        # Real section (line ~210+)
        lines.append("## Item 1. Business")
        lines.append("We are a technology company.")
        lines.extend(["content"] * 20)
        lines.append("## Item 7. Management Discussion")
        lines.append("Revenue grew 15%.")
        lines.extend(["content"] * 20)
        lines.append("## Item 8. Financial Statements")
        lines.append("See attached.")

        segmenter = FilingSegmenter()
        sections = segmenter.segment("\n".join(lines))
        items = [s.item for s in sections]
        # Should have 3 sections (from body), not 9 (from TOC + body)
        assert len(sections) <= 5, f"Too many sections: {items} — TOC entries leaked through"
        # Real body sections should be present
        assert "Item 1" in items
        assert "Item 7" in items

    def test_small_fixture_unaffected(self):
        """Small fixtures with no late matches should not be filtered."""
        from zion_terminal.pipeline.segmenter import FilingSegmenter
        html = (FIXTURES_DIR / "sample_html_filing.html").read_text()
        from zion_terminal.pipeline.converter import FilingConverter
        md = FilingConverter().convert(html)["markdown"]
        segmenter = FilingSegmenter()
        sections = segmenter.segment(md)
        # Small fixture should still find sections normally
        assert len(sections) >= 3


class TestSynthesisRegeneration:
    """A5: Synthesis regeneration scaffold."""

    def test_regeneration_with_validator(self):
        """generate_with_retry should retry and succeed."""
        from zion_terminal.agents.synthesis.agent import SynthesisAgent
        from zion_terminal.agents.validation.agent import ValidationAgent
        agent = SynthesisAgent()
        validator = ValidationAgent()
        result = agent.generate_with_retry(
            "generate company", {"seed": 42}, max_retries=3, validator=validator,
        )
        assert result.success

    def test_regeneration_without_validator(self):
        """Without validator, returns first successful result."""
        from zion_terminal.agents.synthesis.agent import SynthesisAgent
        agent = SynthesisAgent()
        result = agent.generate_with_retry("generate company", {"seed": 42})
        assert result.success

    def test_regeneration_deterministic_across_retries(self):
        """Each retry uses a different seed (base_seed + attempt)."""
        from zion_terminal.agents.synthesis.agent import SynthesisAgent
        agent = SynthesisAgent()
        r1 = agent.generate("test", {"seed": 42})
        r2 = agent.generate("test", {"seed": 43})  # seed + 1 = next retry
        # Different seeds should produce different output
        assert r1.entity_name != r2.entity_name or r1.documents != r2.documents


class TestValidationSchemaExtended:
    """A7: Validation result must include warned/skipped/unavailable counts."""

    def test_validation_result_has_warned_field(self):
        from zion_terminal.models.responses import ValidationResult
        vr = ValidationResult(checks_warned=3)
        assert vr.checks_warned == 3

    def test_validation_result_has_skipped_field(self):
        from zion_terminal.models.responses import ValidationResult
        vr = ValidationResult(checks_skipped=1)
        assert vr.checks_skipped == 1

    def test_validation_result_has_unavailable_field(self):
        from zion_terminal.models.responses import ValidationResult
        vr = ValidationResult(checks_unavailable=2)
        assert vr.checks_unavailable == 2

    def test_warned_checks_counted(self):
        """Validation agent should count warnings."""
        from zion_terminal.agents.validation.agent import ValidationAgent
        from zion_terminal.models.responses import RetrievalResult
        agent = ValidationAgent()
        # Time series with unsorted data produces a warning
        retrieval = RetrievalResult(data=[{
            "source": "test", "data_points": [
                {"date": "2024-03-01", "close": 120},
                {"date": "2024-01-01", "close": 100},
            ],
        }])
        result = agent.validate_retrieval(retrieval)
        assert result.checks_warned >= 1
