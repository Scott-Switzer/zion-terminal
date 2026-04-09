"""Tests for the live verification module."""
import pytest
from zion_terminal.pipeline.verification import FilingVerifier, VerificationResult
from zion_terminal.pipeline.segmenter import FilingSection


@pytest.fixture
def verifier():
    return FilingVerifier()


class TestFilingVerifier:
    def test_structural_pass(self, verifier):
        sections = [
            FilingSection(item="Item 1", title="Business", content="x" * 200),
            FilingSection(item="Item 7", title="MD&A", content="y" * 300),
            FilingSection(item="Item 8", title="Financial Statements", content="z" * 200),
        ]
        result = verifier.verify(
            markdown="x" * 1000,
            sections=sections,
            form="10-K",
        )
        # Without XBRL, cross-source, or company_facts, status is 'structural_only'
        assert result.status == "structural_only"
        assert result.verification_depth == "structural_only"
        assert result.reconciliation_status in ("not_run", "no_company_facts")
        assert any(c["check"] == "markdown_content" and c["status"] == "passed"
                    for c in result.structural_checks)

    def test_structural_missing_sections_warns(self, verifier):
        sections = [
            FilingSection(item="Item 1", title="Business", content="x" * 200),
        ]
        result = verifier.verify(
            markdown="x" * 1000,
            sections=sections,
            form="10-K",
        )
        assert any(c["check"] == "expected_sections" and c["status"] == "warning"
                    for c in result.structural_checks)

    def test_empty_markdown_fails(self, verifier):
        result = verifier.verify(markdown="", sections=[], form="10-K")
        assert result.status == "failed"

    def test_xbrl_unavailable_when_no_arelle(self, verifier):
        result = verifier.verify(
            markdown="x" * 1000,
            sections=[],
            xbrl_url="https://example.com/test.xml",
        )
        # Arelle is not installed in test env, so should be unavailable
        assert result.xbrl_status in ("unavailable", "error")

    def test_verification_result_to_dict(self, verifier):
        result = verifier.verify(markdown="x" * 1000, sections=[])
        d = result.to_dict()
        assert "status" in d
        assert "structural_checks" in d

    def test_no_xbrl_url(self, verifier):
        result = verifier.verify(markdown="x" * 1000, sections=[])
        assert result.xbrl_status == "no_xbrl_url"

    def test_cross_source_with_yahoo_data(self, verifier):
        result = verifier.verify(
            markdown="x" * 1000,
            sections=[],
            ticker="AAPL",
            yahoo_data={"price": 185.0},
        )
        assert result.cross_source_status == "checked"
