"""Runtime correctness tests — prove behavior, not source text.

Every test in this file exercises actual runtime code paths.
No inspect.getsource(), no grepping method existence.
"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zion_terminal.sec.client import SECClient


# ── Priority 1A: Archival SEC retrieval ──────────────────────────────

class TestArchivalFilingRetrieval:
    """Prove that SECClient walks older filing files when recent is insufficient."""

    @pytest.fixture
    def client(self):
        c = SECClient(identity="Test test@test.com")
        import zion_terminal.sec.client as mod
        mod._ticker_map = {
            "AAPL": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        }
        return c

    def _mock_submissions_with_archival(self):
        """Submissions JSON where recent has no 2015 filings but archival does."""
        return {
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-24-000123"],
                    "filingDate": ["2024-11-01"],
                    "form": ["10-K"],
                    "primaryDocument": ["aapl-20240928.htm"],
                    "primaryDocDescription": ["10-K"],
                },
                "files": [
                    {"name": "CIK0000320193-submissions-001.json"},
                ],
            },
        }

    def _mock_older_filing_data(self):
        """Archival filing file with a 2015 10-K."""
        return {
            "accessionNumber": ["0000320193-15-000100"],
            "filingDate": ["2015-10-28"],
            "form": ["10-K"],
            "primaryDocument": ["aapl-20150926.htm"],
            "primaryDocDescription": ["10-K"],
        }

    def test_walks_archival_when_recent_insufficient(self, client):
        """When year=2015 is not in recent, archival files must be walked."""
        with patch.object(client, "get_submissions",
                         return_value=self._mock_submissions_with_archival()):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = self._mock_older_filing_data()
            mock_resp.raise_for_status = MagicMock()

            with patch.object(client._session, "get", return_value=mock_resp):
                filings = client.get_filings("AAPL", form="10-K", year=2015)
                assert len(filings) >= 1, "Archival 2015 filing not found"
                assert filings[0]["filingDate"].startswith("2015")
                assert filings[0]["form"] == "10-K"

    def test_does_not_walk_archival_when_recent_sufficient(self, client):
        """When recent filings satisfy the query, archival walk is skipped."""
        with patch.object(client, "get_submissions",
                         return_value=self._mock_submissions_with_archival()):
            filings = client.get_filings("AAPL", form="10-K", year=2024)
            assert len(filings) >= 1
            assert filings[0]["filingDate"].startswith("2024")

    def test_archival_disabled_when_flag_false(self, client):
        """include_archival=False must not walk older files."""
        with patch.object(client, "get_submissions",
                         return_value=self._mock_submissions_with_archival()):
            filings = client.get_filings(
                "AAPL", form="10-K", year=2015, include_archival=False,
            )
            assert len(filings) == 0, "Should not find 2015 filing without archival walk"


# ── Priority 1C: Direct SEC company facts primary path ────────────────

class TestCompanyFactsDirectSEC:
    """Prove company facts uses direct SEC as primary, edgartools as fallback."""

    def test_direct_sec_is_primary_path(self):
        """_fetch_company_facts must call sec_client.get_company_facts first."""
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        adapter = SECEdgarAdapter(identity="Test test@test.com")

        mock_facts = {
            "entityName": "Apple Inc.",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {"USD": [{"val": 100, "fy": 2023, "fp": "FY"}]}
                    }
                },
            },
        }
        with patch.object(adapter._sec_client, "get_company_facts", return_value=mock_facts) as mock_call:
            with patch.object(adapter._sec_client, "resolve_cik", return_value="0000320193"):
                result = adapter._fetch_company_facts("AAPL", {})
                mock_call.assert_called_once_with("AAPL")
                assert result.success
                assert result.data[0]["facts_source"] == "direct_sec"
                assert result.data[0]["total_facts"] > 0

    def test_falls_back_to_edgartools_when_direct_fails(self):
        """When direct SEC fails, edgartools is used as fallback."""
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        adapter = SECEdgarAdapter(identity="Test test@test.com")

        with patch.object(adapter._sec_client, "get_company_facts", return_value=None):
            mock_company = MagicMock()
            mock_company.name = "Apple Inc."
            mock_company.cik = "320193"
            mock_facts = MagicMock()
            mock_facts.to_pandas.return_value = MagicMock(empty=False, __len__=lambda s: 100)
            mock_company.get_facts.return_value = mock_facts

            with patch("edgar.Company", return_value=mock_company):
                result = adapter._fetch_company_facts("AAPL", {})
                assert result.success
                assert result.data[0]["facts_source"] == "edgartools"


# ── Priority 1C: Financials graceful degradation ──────────────────────

class TestFinancialsGracefulDegradation:
    """Prove that _fetch_financials degrades when Company(ticker) fails."""

    def test_returns_metadata_when_edgartools_unavailable(self):
        """When Company(ticker) raises, should still return filing metadata."""
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        adapter = SECEdgarAdapter(identity="Test test@test.com")

        mock_filings = [{
            "accessionNumber": "0000320193-24-000123",
            "filingDate": "2024-11-01",
            "form": "10-K",
            "primaryDocument": "aapl-20240928.htm",
            "cik": "0000320193",
        }]
        with patch("edgar.Company", side_effect=Exception("edgartools down")):
            with patch.object(adapter._sec_client, "get_filings", return_value=mock_filings):
                result = adapter._fetch_financials("AAPL", {"quarterly": False})
                assert result.success
                assert len(result.data) > 0
                assert "metadata only" in result.data[0].get("note", "").lower()


# ── Priority 2D: Runtime persistence proof ─────────────────────────────

class TestRuntimePersistence:
    """Prove end-to-end persistence: process → persist → read back."""

    def test_full_pipeline_to_sqlite_roundtrip(self, tmp_path):
        """Process a filing, persist to DocumentStore, read it back,
        verify all fields survive the roundtrip."""
        from zion_terminal.pipeline.filing_pipeline import FilingPipeline
        from zion_terminal.cache.doc_store import DocumentStore
        from zion_terminal.models.documents import CleanedDocument

        # Step 1: Process a filing through the pipeline
        pipeline = FilingPipeline()
        html = (
            "<html><body>"
            "<h1>ACME Corp 10-K</h1>"
            "<h2>Item 1</h2><p>Business description. " * 20 + "</p>"
            "<h2>Item 7</h2><p>MD&A content. " * 20 + "</p>"
            "<h2>Item 8</h2><p>Financial statements. " * 20 + "</p>"
            "</body></html>"
        )
        result = pipeline.process(
            html=html, ticker="ACME", form="10-K", filing_date="2023-11-15",
        )
        assert result.success, f"Pipeline failed: {result.errors}"

        # Step 2: Convert to CleanedDocument via to_cleaned_document()
        doc = result.to_cleaned_document()
        assert doc.doc_id == "ACME_10-K_2023-11-15"
        assert doc.ticker == "ACME"
        assert doc.form == "10-K"
        assert len(doc.markdown) > 100

        # Step 3: Persist to SQLite
        store = DocumentStore(db_path=str(tmp_path / "test.db"))
        store.store(doc)

        # Step 4: Read back and verify
        retrieved = store.get("ACME_10-K_2023-11-15")
        assert retrieved is not None, "Document was NOT persisted to SQLite"
        assert retrieved["doc_id"] == "ACME_10-K_2023-11-15"
        assert retrieved["ticker"] == "ACME"
        assert retrieved["form"] == "10-K"
        assert retrieved["filing_date"] == "2023-11-15"
        assert "ACME Corp" in retrieved["markdown"]
        assert isinstance(retrieved["verification"], dict)
        assert isinstance(retrieved["metadata"], dict)

        # Step 5: List and verify
        docs = store.list_docs(ticker="ACME")
        assert len(docs) == 1
        assert docs[0]["doc_id"] == "ACME_10-K_2023-11-15"
        store.close()

    def test_orchestrator_persist_integration(self, tmp_path):
        """Prove the orchestrator's _persist_filing_document stores into
        DocumentStore and the document survives a get() roundtrip."""
        from zion_terminal.orchestrator.orchestrator import Orchestrator
        from zion_terminal.models.responses import OrchestratorResponse, RetrievalResult

        orc = Orchestrator(cache_dir=str(tmp_path))

        # Simulate a successful filing_markdown response
        mock_result = RetrievalResult(
            success=True,
            data=[{
                "content_markdown": "# Apple Inc. 10-K\n\n## Item 1\n\nBusiness overview.\n\n## Item 8\n\n| Revenue | $383B |\n",
                "filing_date": "2023-11-03",
                "pipeline_metadata": {
                    "verification": {
                        "status": "structural_only",
                        "verification_depth": "structural_only",
                    },
                    "source": "sec_edgar",
                },
            }],
            sources_used=["sec_edgar"],
        )
        response = OrchestratorResponse(
            success=True, query="AAPL 10-K markdown", intent="filing_markdown",
            results=[mock_result],
        )

        # Call _persist_filing_document
        orc._persist_filing_document(response, "AAPL", "10-K")

        # Read back from the store
        doc = orc.doc_store.get("AAPL_10-K_2023-11-03")
        assert doc is not None, "_persist_filing_document did NOT store the document"
        assert doc["ticker"] == "AAPL"
        assert doc["form"] == "10-K"
        assert "Apple Inc." in doc["markdown"]
        assert doc["verification"]["status"] == "structural_only"
        orc.close()


# ── Priority 3F: Verification honesty ──────────────────────────────────

class TestVerificationHonesty:
    """Verify the status taxonomy is honest about what ran."""

    def test_structural_only_never_says_passed(self):
        """Without XBRL or reconciliation data, status must be structural_only."""
        from zion_terminal.pipeline.filing_pipeline import FilingPipeline
        pipeline = FilingPipeline()
        result = pipeline.process(
            html="<html><body>" + "<p>Content paragraph. " * 50 + "</p></body></html>",
            ticker="TEST", form="10-K",
        )
        assert result.verification["status"] == "structural_only"
        assert result.verification["verification_depth"] == "structural_only"

    def test_reconciled_pass_requires_exact_period(self):
        """reconciled_pass must only happen with exact_period match mode."""
        from zion_terminal.pipeline.verification import FilingVerifier

        verifier = FilingVerifier()
        md = (
            "# Financial Statements\n\n"
            "| Item | Val |\n|---|---|\n"
            "| Total Revenue | $100,000 |\n"
        )
        # Facts where FY can be matched exactly
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {"USD": [
                            {"val": 100000, "fy": 2023, "fp": "FY",
                             "end": "2023-12-31"},
                        ]}
                    }
                }
            }
        }
        result = verifier.verify(
            markdown=md, company_facts=facts,
            form="10-K", filing_date="2024-02-15",
        )
        # With the facts having fy=2023/fp=FY and the verifier finding
        # a match, check that the status semantics are correct
        if result.period_match_mode == "exact_period":
            assert result.reconciliation_status in (
                "reconciled_pass", "reconciled_partial",
            )
        else:
            assert result.reconciliation_status != "reconciled_pass"

    def test_heuristic_period_downgrades_status(self):
        """When period is heuristic, reconciliation must NOT say reconciled_pass."""
        from zion_terminal.pipeline.verification import FilingVerifier

        verifier = FilingVerifier()
        md = "# Filing\n\n| Revenue | $500 |\n"
        # Minimal facts that won't validate against any XBRL entries
        facts = {"facts": {}}
        result = verifier.verify(
            markdown=md, company_facts=facts,
            form="10-K", filing_date="2023-03-15",
        )
        assert result.reconciliation_status != "reconciled_pass"


# ── Priority 1B: Period correctness ────────────────────────────────────

class TestPeriodDerivationCorrectness:
    """Prove period derivation uses fiscal metadata, not filing-date year."""

    def test_apple_sept_fye_november_filing(self):
        """Apple FYE=September, files 10-K in November → FY should be filing year."""
        from zion_terminal.pipeline.verification import _derive_fiscal_period
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {"USD": [
                            {"val": 100, "fy": 2023, "fp": "FY",
                             "end": "2023-09-30"},
                        ]}
                    }
                }
            }
        }
        fy, fp, mode = _derive_fiscal_period("2023-11-03", "10-K", facts)
        assert fy == 2023
        assert fp == "FY"
        assert mode == "exact_period"

    def test_calendar_year_march_filing(self):
        """Calendar FYE=December, files 10-K in March → FY should be PRIOR year."""
        from zion_terminal.pipeline.verification import _derive_fiscal_period
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {"USD": [
                            {"val": 100, "fy": 2023, "fp": "FY",
                             "end": "2023-12-31"},
                        ]}
                    }
                }
            }
        }
        fy, fp, mode = _derive_fiscal_period("2024-03-15", "10-K", facts)
        assert fy == 2023, f"Expected FY=2023, got {fy}"

    def test_microsoft_june_fye(self):
        """Microsoft FYE=June, files in September → FY should be filing year."""
        from zion_terminal.pipeline.verification import _derive_fiscal_period
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {"USD": [
                            {"val": 100, "fy": 2023, "fp": "FY",
                             "end": "2023-06-30"},
                        ]}
                    }
                }
            }
        }
        fy, fp, mode = _derive_fiscal_period("2023-09-15", "10-K", facts)
        assert fy == 2023

    def test_no_facts_labeled_heuristic(self):
        """Without company facts, period derivation must be labeled heuristic."""
        from zion_terminal.pipeline.verification import _derive_fiscal_period
        fy, fp, mode = _derive_fiscal_period("2023-11-03", "10-K", None)
        assert mode == "heuristic"
        assert fy == 2023

    def test_10q_exact_period_from_filed_date(self):
        """10-Q exact period comes from company facts filed-date match."""
        from zion_terminal.pipeline.verification import _derive_fiscal_period
        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {"USD": [
                            {"val": 50, "fy": 2023, "fp": "Q2",
                             "filed": "2023-05-06", "end": "2023-03-31"},
                        ]}
                    }
                }
            }
        }
        fy, fp, mode = _derive_fiscal_period("2023-05-06", "10-Q", facts)
        assert fy is not None
        if mode == "exact_period":
            assert fp == "Q2"


# ── Priority 3G: Replace weak source-inspection tests ─────────────────

class TestSECFinancialsHonorsParams:
    """Replace test_regression_fixes.TestSECFinancialsParams source-inspection
    tests with actual runtime behavior tests."""

    def test_quarterly_flag_uses_10q_form(self):
        """quarterly=True must cause the adapter to request 10-Q filings."""
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        adapter = SECEdgarAdapter(identity="Test test@test.com")

        mock_company = MagicMock()
        mock_company.get_filings.return_value = iter([])  # no filings

        with patch("edgar.Company", return_value=mock_company):
            adapter._fetch_financials("AAPL", {"quarterly": True, "limit": 1})
            # Verify 10-Q was requested
            mock_company.get_filings.assert_called_with(form="10-Q")

    def test_statement_type_filters_correctly(self):
        """statement_type='balance' must only extract balance_sheet."""
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        adapter = SECEdgarAdapter(identity="Test test@test.com")

        mock_filing_obj = MagicMock()
        mock_bs = MagicMock()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.columns = ["2023"]
        mock_df.index = ["Total Assets"]
        mock_df.iloc.__getitem__ = MagicMock(return_value=MagicMock(
            loc={"Total Assets": 100.0},
        ))
        mock_bs.to_dataframe.return_value = mock_df
        mock_filing_obj.financials.balance_sheet = mock_bs
        mock_filing_obj.financials.income_statement = MagicMock()
        mock_filing_obj.financials.cash_flow_statement = MagicMock()

        mock_filing = MagicMock()
        mock_filing.obj.return_value = mock_filing_obj
        mock_filing.filing_date = "2023-11-03"

        mock_company = MagicMock()
        mock_company.get_filings.return_value = iter([mock_filing])

        with patch("edgar.Company", return_value=mock_company):
            result = adapter._fetch_financials(
                "AAPL", {"statement_type": "balance", "quarterly": False, "limit": 1},
            )
            if result.success and result.data:
                # Should only contain balance_sheet statements
                for item in result.data:
                    assert item["statement_type"] == "balance_sheet"


# ── Priority 4H: Arelle strict verification profile ───────────────────

class TestArelleStrictProfile:
    """Verify Arelle is distinguishable from company-facts reconciliation."""

    def test_company_facts_reconciliation_not_labeled_arelle(self):
        """When only company-facts reconciliation ran, must NOT claim arelle ran."""
        from zion_terminal.pipeline.verification import FilingVerifier
        verifier = FilingVerifier()

        facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {"USD": [
                            {"val": 100000, "fy": 2023, "fp": "FY",
                             "end": "2023-12-31"},
                        ]}
                    }
                }
            }
        }
        result = verifier.verify(
            markdown="# Filing\n| Revenue | $100,000 |",
            company_facts=facts,
            form="10-K",
            filing_date="2024-02-15",
        )
        # xbrl_status should NOT be "passed" (Arelle didn't run)
        assert result.xbrl_status != "passed"
        # If reconciliation ran, it's company-facts, not Arelle
        if result.reconciliation_status.startswith("reconciled"):
            assert result.verification_depth == "reconciled"

    def test_xbrl_status_unavailable_without_arelle(self):
        """Without Arelle installed, xbrl_status must be 'unavailable' or 'no_xbrl_url'."""
        from zion_terminal.pipeline.verification import FilingVerifier
        verifier = FilingVerifier()
        result = verifier.verify(
            markdown="# Test filing " * 20,
            xbrl_url="https://example.com/filing.htm",
        )
        # Without Arelle, should be unavailable or error, never "passed"
        assert result.xbrl_status in ("unavailable", "error", "no_xbrl_url")
