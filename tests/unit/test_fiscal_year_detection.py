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
    """Prove that get_filing_markdown persists documents at runtime.

    These tests exercise the actual live path, not just method existence.
    """

    def test_persist_creates_document_in_store(self, tmp_path):
        """_persist_filing_document must actually store a CleanedDocument."""
        from zion_terminal.orchestrator.orchestrator import Orchestrator
        from zion_terminal.models.responses import OrchestratorResponse, RetrievalResult

        orc = Orchestrator(cache_dir=str(tmp_path))
        mock_result = RetrievalResult(
            success=True,
            data=[{
                "content_markdown": "# Apple 10-K\n\nFinancial data here.",
                "filing_date": "2023-11-03",
                "pipeline_metadata": {"verification": {"status": "structural_only"}},
            }],
            sources_used=["sec_edgar"],
        )
        response = OrchestratorResponse(
            success=True, query="test", intent="filing_markdown",
            results=[mock_result],
        )

        orc._persist_filing_document(response, "AAPL", "10-K")

        # Prove document was stored
        doc = orc.doc_store.get("AAPL_10-K_2023-11-03")
        assert doc is not None, "Document was NOT persisted"
        assert doc["ticker"] == "AAPL"
        assert doc["form"] == "10-K"
        assert "Apple 10-K" in doc["markdown"]
        orc.close()

    def test_get_filing_markdown_calls_persist(self, tmp_path):
        """get_filing_markdown must call _persist on success."""
        from unittest.mock import patch, MagicMock
        from zion_terminal.orchestrator.orchestrator import Orchestrator
        from zion_terminal.models.responses import OrchestratorResponse, RetrievalResult

        orc = Orchestrator(cache_dir=str(tmp_path))
        mock_result = RetrievalResult(
            success=True,
            data=[{"content_markdown": "# Test", "filing_date": "2023-01-01"}],
            sources_used=["sec_edgar"],
        )
        mock_resp = OrchestratorResponse(
            success=True, query="t", intent="filing_markdown",
            results=[mock_result],
        )
        with patch.object(orc, "_fetch_and_validate", return_value=mock_resp):
            with patch.object(orc, "_persist_filing_document") as mock_persist:
                orc.get_filing_markdown("AAPL", form="10-K")
                assert mock_persist.called, "_persist was NOT called by get_filing_markdown"
        orc.close()

    def test_persist_does_not_run_on_failure(self, tmp_path):
        """Persistence must NOT run when the response failed."""
        from unittest.mock import patch
        from zion_terminal.orchestrator.orchestrator import Orchestrator
        from zion_terminal.models.responses import OrchestratorResponse

        orc = Orchestrator(cache_dir=str(tmp_path))
        mock_resp = OrchestratorResponse(
            success=False, query="t", intent="filing_markdown",
            errors=["SEC error"],
        )
        with patch.object(orc, "_fetch_and_validate", return_value=mock_resp):
            with patch.object(orc, "_persist_filing_document") as mock_persist:
                orc.get_filing_markdown("AAPL", form="10-K")
                assert not mock_persist.called, "_persist should NOT run on failure"
        orc.close()

    def test_doc_store_list_after_persist(self, tmp_path):
        """After persistence, the document should be listable."""
        from zion_terminal.orchestrator.orchestrator import Orchestrator
        from zion_terminal.models.responses import OrchestratorResponse, RetrievalResult

        orc = Orchestrator(cache_dir=str(tmp_path))
        mock_result = RetrievalResult(
            success=True,
            data=[{"content_markdown": "# Filing", "filing_date": "2023-06-15"}],
            sources_used=["sec_edgar"],
        )
        response = OrchestratorResponse(
            success=True, query="t", intent="filing_markdown",
            results=[mock_result],
        )
        orc._persist_filing_document(response, "MSFT", "10-Q")

        docs = orc.doc_store.list_docs(ticker="MSFT")
        assert len(docs) >= 1, f"Expected at least 1 doc, got {len(docs)}"
        assert docs[0]["ticker"] == "MSFT"
        orc.close()
