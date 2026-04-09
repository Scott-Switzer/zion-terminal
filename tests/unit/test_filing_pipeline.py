"""Tests for the unified filing pipeline."""
from pathlib import Path
import pytest
from zion_terminal.pipeline.filing_pipeline import FilingPipeline, FilingPipelineResult

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def pipeline():
    return FilingPipeline()


@pytest.fixture
def sample_html():
    return (FIXTURES_DIR / "sample_html_filing.html").read_text()


@pytest.fixture
def div_html():
    return (FIXTURES_DIR / "sample_html_filing_div_headers.html").read_text()


class TestFilingPipeline:
    def test_process_basic(self, pipeline, sample_html):
        result = pipeline.process(sample_html, ticker="TEST", form="10-K")
        assert result.success
        assert result.markdown_char_count > 0
        assert result.converter_engine == "dom"
        assert result.section_count >= 3

    def test_result_has_pipeline_metadata(self, pipeline, sample_html):
        result = pipeline.process(sample_html, ticker="TEST", form="10-K")
        assert "conversion" in result.pipeline_metadata
        assert "segmentation" in result.pipeline_metadata
        assert result.pipeline_metadata["source_role"] == "primary"

    def test_result_to_dict(self, pipeline, sample_html):
        result = pipeline.process(sample_html, ticker="TEST", form="10-K")
        d = result.to_dict()
        assert d["success"] is True
        assert "sections_summary" in d
        assert d["ticker"] == "TEST"
        assert d["form"] == "10-K"

    def test_div_header_filing(self, pipeline, div_html):
        result = pipeline.process(div_html, ticker="REAL", form="10-K")
        assert result.success
        items = {s.item for s in result.sections}
        assert "Item 1" in items
        assert "Item 7" in items

    def test_empty_html_fails(self, pipeline):
        result = pipeline.process("", ticker="TEST", form="10-K")
        # Empty HTML should produce an empty markdown (success but empty)
        assert result.success or len(result.errors) > 0

    def test_verification_hooks_present(self, pipeline, sample_html):
        """Verification is now live — status must not be 'pending'."""
        result = pipeline.process(sample_html)
        assert "status" in result.verification
        assert result.verification["status"] != "pending", (
            "Verification status is still placeholder 'pending'"
        )
        # 'structural_only' is the honest status when only structural checks ran
        assert result.verification["status"] in ("structural_only", "passed", "partial", "failed", "error")

    def test_pipeline_is_the_only_converter_path(self):
        """Regression: sec_edgar adapter must NOT have its own _html_to_markdown."""
        import inspect
        from zion_terminal.agents.retrieval.adapters import sec_edgar
        source = inspect.getsource(sec_edgar)
        assert "def _html_to_markdown" not in source, (
            "sec_edgar still has a private _html_to_markdown — "
            "all conversion must go through FilingPipeline"
        )

    def test_sec_adapter_uses_pipeline(self):
        """SEC adapter must import and use FilingPipeline."""
        import inspect
        from zion_terminal.agents.retrieval.adapters import sec_edgar
        source = inspect.getsource(sec_edgar)
        assert "FilingPipeline" in source, (
            "sec_edgar must use the shared FilingPipeline"
        )
