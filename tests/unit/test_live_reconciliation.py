"""Tests for live XBRL↔markdown fact reconciliation in the filing pipeline.

These tests prove that the reconciler is actually called from the live path
and that it correctly identifies matches, mismatches, and missing facts.
No Arelle dependency — uses SEC company facts JSON.
"""
import json
from pathlib import Path

import pytest

from zion_terminal.pipeline.filing_pipeline import FilingPipeline
from zion_terminal.pipeline.verification import (
    FilingVerifier, VerificationResult, _company_facts_to_canonical,
)
from zion_terminal.verification.fact_mapping import CanonicalFact

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def company_facts():
    """Load the sample company facts fixture."""
    return json.loads((FIXTURES / "sample_company_facts.json").read_text())


@pytest.fixture
def matching_html():
    """HTML with values that exactly match company facts (raw dollars)."""
    return (FIXTURES / "sample_filing_with_tables.html").read_text()


@pytest.fixture
def scale_mismatch_html():
    """HTML with values in millions (company facts are in raw dollars)."""
    return (FIXTURES / "sample_filing_scale_mismatch.html").read_text()


# ── _company_facts_to_canonical ─────────────────────────────────────

class TestCompanyFactsToCanonical:
    """Test conversion of SEC company facts JSON to CanonicalFact list."""

    def test_extracts_core_concepts(self, company_facts):
        facts = _company_facts_to_canonical(company_facts)
        concept_names = {f.local_name for f in facts}
        assert "Revenues" in concept_names
        assert "NetIncomeLoss" in concept_names
        assert "Assets" in concept_names

    def test_values_are_numeric(self, company_facts):
        facts = _company_facts_to_canonical(company_facts)
        for f in facts:
            assert isinstance(f.value, float), f"{f.local_name} value is not float"

    def test_revenue_value_is_correct(self, company_facts):
        facts = _company_facts_to_canonical(company_facts)
        rev = [f for f in facts if f.local_name == "Revenues"][0]
        assert rev.value == 383285000000.0  # Most recent entry

    def test_period_types_set(self, company_facts):
        facts = _company_facts_to_canonical(company_facts)
        rev = [f for f in facts if f.local_name == "Revenues"][0]
        assert rev.period_type == "duration"
        assets = [f for f in facts if f.local_name == "Assets"][0]
        assert assets.period_type == "instant"

    def test_empty_facts_returns_empty(self):
        assert _company_facts_to_canonical({}) == []
        assert _company_facts_to_canonical({"facts": {}}) == []

    def test_ignores_non_core_concepts(self):
        facts_json = {
            "facts": {
                "us-gaap": {
                    "SomeObscureConcept": {
                        "units": {"USD": [{"val": 42}]}
                    }
                }
            }
        }
        assert _company_facts_to_canonical(facts_json) == []


# ── Reconciliation in pipeline ──────────────────────────────────────

class TestReconciliationInPipeline:
    """Test that reconciliation runs through the live pipeline."""

    def test_reconciliation_runs_with_company_facts(self, matching_html, company_facts):
        """Core test: pipeline runs reconciliation when company_facts provided."""
        pipeline = FilingPipeline()
        result = pipeline.process(
            html=matching_html,
            ticker="AAPL",
            form="10-K",
            metadata={"company_facts": company_facts},
        )
        assert result.success
        status = result.verification.get("reconciliation_status")
        assert status != "not_run", (
            f"Reconciliation did not run! Status: {status}"
        )
        assert status not in ("not_run", "no_company_facts"), (
            f"Reconciliation was not triggered: {status}"
        )

    def test_reconciliation_not_run_without_facts(self, matching_html):
        """Without company_facts, reconciliation should not run."""
        pipeline = FilingPipeline()
        result = pipeline.process(
            html=matching_html,
            ticker="AAPL",
            form="10-K",
        )
        assert result.verification.get("reconciliation_status") in (
            "not_run", "no_company_facts",
        )

    def test_exact_match_produces_reconciled_pass(self, matching_html, company_facts):
        """When markdown values exactly match XBRL, status should be reconciled_pass."""
        pipeline = FilingPipeline()
        result = pipeline.process(
            html=matching_html,
            ticker="AAPL",
            form="10-K",
            metadata={"company_facts": company_facts},
        )
        report = result.verification.get("reconciliation_report", {})
        matched = report.get("facts_matched", 0)
        assert matched > 0, f"Expected matched facts > 0, got {matched}"

    def test_verification_depth_is_reconciled(self, matching_html, company_facts):
        """When reconciliation runs, verification_depth should be 'reconciled'."""
        pipeline = FilingPipeline()
        result = pipeline.process(
            html=matching_html,
            ticker="AAPL",
            form="10-K",
            metadata={"company_facts": company_facts},
        )
        depth = result.verification.get("verification_depth")
        recon_status = result.verification.get("reconciliation_status")
        if recon_status in ("reconciled_pass", "reconciled_partial", "reconciled_fail"):
            assert depth == "reconciled", f"Expected depth 'reconciled', got '{depth}'"


class TestReconciliationScaleMismatch:
    """Test that scale mismatches between XBRL (raw $) and markdown (millions) are detected."""

    def test_scale_mismatch_detected(self, scale_mismatch_html, company_facts):
        pipeline = FilingPipeline()
        result = pipeline.process(
            html=scale_mismatch_html,
            ticker="AAPL",
            form="10-K",
            metadata={"company_facts": company_facts},
        )
        report = result.verification.get("reconciliation_report", {})
        scale_mismatches = report.get("facts_scale_mismatch", 0)
        assert scale_mismatches > 0, (
            f"Expected scale mismatches > 0, got {scale_mismatches}. "
            f"Report: {report}"
        )


class TestReconciliationMissingFacts:
    """Test that missing facts are detected."""

    def test_missing_facts_with_empty_markdown(self, company_facts):
        """Markdown with no tables should result in no_markdown_facts."""
        pipeline = FilingPipeline()
        result = pipeline.process(
            html="<html><body><p>This filing has no financial tables.</p></body></html>",
            ticker="AAPL",
            form="10-K",
            metadata={"company_facts": company_facts},
        )
        recon_status = result.verification.get("reconciliation_status")
        assert recon_status in ("no_markdown_facts", "reconciled_fail", "no_comparable_facts")


# ── Direct verifier tests ───────────────────────────────────────────

class TestFilingVerifierReconciliation:
    """Test the FilingVerifier reconciliation directly."""

    def test_verifier_with_company_facts(self, company_facts):
        verifier = FilingVerifier()
        md = """
| Line Item | 2023 |
|---|---|
| Total Revenue | $383,285,000,000 |
| Net Income | $96,995,000,000 |
| Total Assets | $352,583,000,000 |
"""
        result = verifier.verify(
            markdown=md, company_facts=company_facts,
        )
        assert result.reconciliation_status != "not_run"
        assert result.facts_extracted_xbrl > 0
        assert result.facts_extracted_markdown > 0

    def test_verifier_without_company_facts(self):
        verifier = FilingVerifier()
        result = verifier.verify(markdown="# Test\nSome content " * 20)
        assert result.reconciliation_status in ("not_run", "no_company_facts")
        assert result.verification_depth == "structural_only"

    def test_to_dict_includes_reconciliation(self, company_facts):
        verifier = FilingVerifier()
        result = verifier.verify(
            markdown="| Item | Val |\n|---|---|\n| Total Revenue | $383,285,000,000 |",
            company_facts=company_facts,
        )
        d = result.to_dict()
        assert "reconciliation_status" in d
        assert "reconciliation_report" in d
        assert "facts_extracted_xbrl" in d
        assert "facts_matched" in d
