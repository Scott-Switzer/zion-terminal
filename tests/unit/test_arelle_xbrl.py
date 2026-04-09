"""Arelle-backed XBRL tests — strict verification profile.

These tests require arelle-release. They skip (not fail) when Arelle
is not installed, but a CI/verification environment MUST have Arelle.

Run the strict profile explicitly::

    pip install arelle-release
    python -m pytest tests/unit/test_arelle_xbrl.py -v

If this test file shows "SKIPPED" in CI, the XBRL verification profile
is NOT satisfied and should be treated as a gap.
"""
import pytest

arelle = pytest.importorskip("arelle", reason="arelle-release not installed — XBRL verification profile NOT satisfied")

from zion_terminal.pipeline.xbrl import XBRLVerifier, is_available, XBRLValidationResult


class TestArelleAvailability:
    def test_arelle_is_available(self):
        """Arelle must report as available when installed."""
        assert is_available() is True

    def test_verifier_reports_available(self):
        v = XBRLVerifier()
        assert v.is_available is True


class TestArelleFactExtraction:
    def test_extract_concept_name_handles_none(self):
        """_extract_concept_name should not crash on None."""
        v = XBRLVerifier()
        assert v._extract_concept_name(None) == ""

    def test_extract_unit_handles_none(self):
        """_extract_unit should not crash on None."""
        v = XBRLVerifier()
        assert v._extract_unit(None) is None


class TestArelleErrorHandling:
    def test_invalid_url_returns_error(self):
        """Loading a non-existent URL should return an error result, not crash."""
        v = XBRLVerifier()
        result = v.validate_url("file:///nonexistent/path/does_not_exist.xml")
        # Should return a result object, not raise
        assert isinstance(result, XBRLValidationResult)
        assert not result.valid or len(result.errors) > 0 or len(result.warnings) > 0

    def test_empty_string_returns_error(self):
        v = XBRLVerifier()
        result = v.validate_url("")
        assert isinstance(result, XBRLValidationResult)
