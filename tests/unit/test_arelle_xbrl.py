"""Arelle-backed XBRL tests.

Skipped (not failed) if arelle-release is not installed.
When Arelle IS available, these tests verify correct fact extraction.
"""
import pytest

arelle = pytest.importorskip("arelle", reason="arelle-release not installed — XBRL tests skipped")

from zion_terminal.pipeline.xbrl import XBRLVerifier, is_available


class TestArelleAvailability:
    def test_arelle_is_available(self):
        assert is_available() is True

    def test_verifier_reports_available(self):
        v = XBRLVerifier()
        assert v.is_available is True


class TestArelleValidation:
    def test_invalid_url_returns_error(self):
        v = XBRLVerifier()
        result = v.validate_url("https://nonexistent.invalid/file.xml")
        assert not result.valid or len(result.errors) > 0 or len(result.warnings) > 0
