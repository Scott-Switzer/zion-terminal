"""Tests for fiscal year end month detection and improved FY derivation.

Tests that:
- FYE month is correctly detected from XBRL period-end dates
- Non-calendar fiscal year companies get correct candidate_fy
- FY derivation falls back to heuristic when company facts unavailable
"""
import pytest

from zion_terminal.pipeline.verification import (
    _detect_fiscal_year_end_month, _derive_fiscal_period,
)


class TestFiscalYearEndMonthDetection:
    """Test _detect_fiscal_year_end_month()."""

    def test_apple_september_fye(self):
        """Apple's FY ends in September (month 9)."""
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"val": 100, "fy": 2023, "fp": "FY", "end": "2023-09-30"},
                                {"val": 90, "fy": 2022, "fp": "FY", "end": "2022-09-24"},
                            ]
                        }
                    }
                }
            }
        }
        assert _detect_fiscal_year_end_month(facts) == 9

    def test_calendar_year_december_fye(self):
        """Most companies have December FYE (month 12)."""
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"val": 100, "fy": 2023, "fp": "FY", "end": "2023-12-31"},
                            ]
                        }
                    }
                }
            }
        }
        assert _detect_fiscal_year_end_month(facts) == 12

    def test_june_fye(self):
        """Companies like Microsoft have June FYE (month 6)."""
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"val": 100, "fy": 2023, "fp": "FY", "end": "2023-06-30"},
                            ]
                        }
                    }
                }
            }
        }
        assert _detect_fiscal_year_end_month(facts) == 6

    def test_no_company_facts_returns_none(self):
        assert _detect_fiscal_year_end_month(None) is None
        assert _detect_fiscal_year_end_month({}) is None

    def test_no_fy_entries_returns_none(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"val": 100, "fy": 2023, "fp": "Q1", "end": "2023-03-31"},
                            ]
                        }
                    }
                }
            }
        }
        assert _detect_fiscal_year_end_month(facts) is None


class TestImprovedFYDerivation:
    """Test _derive_fiscal_period with FYE month awareness."""

    def test_apple_10k_november_filing(self):
        """Apple files 10-K in November, FY ends September → FY=filing year."""
        facts = self._make_facts(fye_month=9, fy=2023)
        fy, fp, mode = _derive_fiscal_period("2023-11-03", "10-K", facts)
        assert fy == 2023
        assert fp == "FY"
        assert mode == "exact_period"

    def test_december_fye_march_filing(self):
        """Calendar-year company files 10-K in March → FY=prior year."""
        facts = self._make_facts(fye_month=12, fy=2023)
        fy, fp, mode = _derive_fiscal_period("2024-03-15", "10-K", facts)
        assert fy == 2023
        assert fp == "FY"

    def test_june_fye_september_filing(self):
        """Microsoft-style: FY ends June, files 10-K in Sep → FY=filing year."""
        facts = self._make_facts(fye_month=6, fy=2023)
        fy, fp, mode = _derive_fiscal_period("2023-09-15", "10-K", facts)
        assert fy == 2023

    def test_no_facts_falls_back_to_heuristic(self):
        fy, fp, mode = _derive_fiscal_period("2023-11-03", "10-K", None)
        assert fy == 2023
        assert mode == "heuristic"

    @staticmethod
    def _make_facts(fye_month: int, fy: int) -> dict:
        end_date = f"{fy}-{fye_month:02d}-30"
        return {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"val": 100, "fy": fy, "fp": "FY", "end": end_date, "filed": f"{fy}-{fye_month + 2:02d}-15" if fye_month <= 10 else f"{fy + 1}-01-15"},
                            ]
                        }
                    }
                }
            }
        }


class TestLiveDocumentPersistence:
    """Test that get_filing_markdown persists documents via DocumentStore."""

    def test_orchestrator_has_doc_store(self):
        """Orchestrator must have a doc_store property."""
        from zion_terminal.orchestrator.orchestrator import Orchestrator
        orc = Orchestrator()
        assert hasattr(orc, "doc_store")
        assert orc.doc_store is not None
        orc.close()

    def test_persist_filing_document_method_exists(self):
        """The persistence method must exist on the orchestrator."""
        from zion_terminal.orchestrator.orchestrator import Orchestrator
        assert hasattr(Orchestrator, "_persist_filing_document")
