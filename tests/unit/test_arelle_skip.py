"""Tests verifying graceful XBRL behavior regardless of Arelle availability."""
import pytest

from zion_terminal.pipeline.verification import FilingVerifier


class TestWithoutArelle:
    def test_verification_without_xbrl_is_structural_only(self):
        """Without company_facts or xbrl_url, verification is structural_only."""
        verifier = FilingVerifier()
        result = verifier.verify(markdown="# Test\n" + "content " * 50)
        assert result.xbrl_status in ("no_xbrl_url", "not_run", "unavailable")
        assert result.reconciliation_status in ("not_run", "no_company_facts")

    def test_xbrl_module_importable(self):
        """The XBRL module should always be importable, even without Arelle."""
        from zion_terminal.pipeline import xbrl
        assert hasattr(xbrl, "is_available")
        assert hasattr(xbrl, "XBRLVerifier")
