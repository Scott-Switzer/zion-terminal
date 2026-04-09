"""Regression tests for critical fixes identified in the 2026-04-08 audit.

Each test guards a specific bug so it cannot silently return.
"""
import hashlib
import subprocess
import sys

import pytest

from zion_terminal.agents.synthesis.agent import SynthesisAgent
from zion_terminal.orchestrator.intent_parser import IntentParser


# ── FIX A: NL financials query MUST route to SEC, not Yahoo ──────────

class TestNLFinancialsRoutesToSEC:
    """Guard: intent_parser._build_equity_task routes financials to sec_edgar."""

    def setup_method(self):
        self.parser = IntentParser()

    def test_nl_financials_routes_to_sec(self):
        parsed = self.parser.parse("Show me AAPL financials")
        assert parsed.tasks, "No tasks generated"
        for task in parsed.tasks:
            if task.get("action") == "financials":
                assert task["source"] == "sec_edgar", (
                    f"NL financials routed to {task['source']}, expected sec_edgar"
                )

    def test_nl_income_statement_routes_to_sec(self):
        parsed = self.parser.parse("AAPL income statement")
        financial_tasks = [t for t in parsed.tasks if t.get("action") == "financials"]
        assert financial_tasks, "No financial tasks generated"
        assert financial_tasks[0]["source"] == "sec_edgar"

    def test_nl_balance_sheet_routes_to_sec(self):
        parsed = self.parser.parse("AAPL quarterly balance sheet")
        financial_tasks = [t for t in parsed.tasks if t.get("action") == "financials"]
        assert financial_tasks, "No financial tasks generated"
        assert financial_tasks[0]["source"] == "sec_edgar"
        assert financial_tasks[0]["statement_type"] == "balance"
        assert financial_tasks[0]["quarterly"] is True

    def test_nl_cash_flow_routes_to_sec(self):
        parsed = self.parser.parse("AAPL cash flow statement")
        financial_tasks = [t for t in parsed.tasks if t.get("action") == "financials"]
        assert financial_tasks, "No financial tasks generated"
        assert financial_tasks[0]["source"] == "sec_edgar"
        assert financial_tasks[0]["statement_type"] == "cash_flow"

    def test_nl_quote_still_routes_to_yahoo(self):
        """Quote is market data — Yahoo is correct."""
        parsed = self.parser.parse("AAPL stock price")
        assert parsed.tasks
        assert parsed.tasks[0]["source"] == "yahoo_finance"

    def test_nl_history_still_routes_to_yahoo(self):
        """History is market data — Yahoo is correct."""
        parsed = self.parser.parse("AAPL historical prices")
        assert parsed.tasks
        assert parsed.tasks[0]["source"] == "yahoo_finance"


# ── FIX B: SEC financials MUST honor statement_type and quarterly ────

class TestSECFinancialsParams:
    """Guard: _fetch_financials reads statement_type and quarterly."""

    def test_sec_adapter_has_stmt_attr_map(self):
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        assert hasattr(SECEdgarAdapter, "_STMT_ATTR_MAP")
        mapping = SECEdgarAdapter._STMT_ATTR_MAP
        assert mapping["income"] == "income_statement"
        assert mapping["balance"] == "balance_sheet"
        assert mapping["cash_flow"] == "cash_flow_statement"

    def test_sec_adapter_fetch_financials_reads_quarterly(self):
        """Verify the code path checks the quarterly flag."""
        import inspect
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        source = inspect.getsource(SECEdgarAdapter._fetch_financials.__wrapped__)
        assert "10-Q" in source, "SEC financials doesn't check for 10-Q"
        assert "quarterly" in source, "SEC financials doesn't read quarterly param"

    def test_sec_adapter_fetch_financials_reads_statement_type(self):
        """Verify the code path checks statement_type."""
        import inspect
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        source = inspect.getsource(SECEdgarAdapter._fetch_financials.__wrapped__)
        assert "statement_type" in source, "SEC financials doesn't read statement_type param"

    def test_sec_adapter_supports_year_param(self):
        """Verify the SEC adapter accepts year parameter."""
        import inspect
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        source = inspect.getsource(SECEdgarAdapter._fetch_financials.__wrapped__)
        assert "year" in source, "SEC financials doesn't read year param"


# ── FIX C: Synthesis determinism uses stable hash ────────────────────

class TestStableDeterminism:
    """Guard: synthesis uses hashlib, not hash()."""

    def test_uses_hashlib_not_builtin_hash(self):
        import inspect
        source = inspect.getsource(SynthesisAgent.generate)
        assert "hashlib" in source, "SynthesisAgent.generate must use hashlib"
        assert "hash(query)" not in source, "SynthesisAgent.generate must NOT use hash()"

    def test_cross_process_determinism(self):
        """Run synthesis in a subprocess with different PYTHONHASHSEED and verify same output."""
        script = """
import json, sys
sys.path.insert(0, 'src')
from zion_terminal.agents.synthesis.agent import SynthesisAgent
agent = SynthesisAgent()
result = agent.generate("test company generation")
print(json.dumps(result.entity_name))
"""
        results = []
        for seed_val in ["0", "42", "999"]:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True,
                cwd=str(__import__("pathlib").Path(__file__).parent.parent.parent),
                env={**__import__("os").environ, "PYTHONHASHSEED": seed_val},
            )
            assert proc.returncode == 0, f"Subprocess failed: {proc.stderr}"
            results.append(proc.stdout.strip())

        assert results[0] == results[1] == results[2], (
            f"Cross-process determinism failed: {results}"
        )

    def test_same_seed_same_output(self):
        agent = SynthesisAgent()
        r1 = agent.generate("stable test", {"seed": 42})
        r2 = agent.generate("stable test", {"seed": 42})
        assert r1.documents == r2.documents

    def test_different_seeds_diverge(self):
        agent = SynthesisAgent()
        r1 = agent.generate("test", {"seed": 1})
        r2 = agent.generate("test", {"seed": 9999})
        assert r1.entity_name != r2.entity_name or r1.entity_ticker != r2.entity_ticker


# ── FIX D: Filing pipeline verification is wired ─────────────────────

class TestFilingPipelineVerification:
    """Guard: FilingPipeline.process() runs verification, not just placeholder."""

    def test_verification_has_real_status(self):
        from zion_terminal.pipeline.filing_pipeline import FilingPipeline
        pipeline = FilingPipeline()
        result = pipeline.process(
            html="<html><body><h1>Test Filing</h1><p>Content here with enough text to pass structural check. " * 10 + "</p></body></html>",
            ticker="TEST",
            form="10-K",
        )
        assert result.success
        assert result.verification.get("status") != "pending", (
            "Verification status is still 'pending' — verification is not wired in"
        )
        # Without XBRL or cross-source, status should be 'structural_only' (honest)
        assert result.verification.get("status") in ("structural_only", "passed", "partial", "failed"), (
            f"Unexpected verification status: {result.verification.get('status')}"
        )

    def test_verification_has_structural_checks(self):
        from zion_terminal.pipeline.filing_pipeline import FilingPipeline
        pipeline = FilingPipeline()
        result = pipeline.process(
            html="<html><body>" + "<p>x</p>" * 50 + "</body></html>",
            ticker="TEST",
            form="10-K",
        )
        checks = result.verification.get("structural_checks", [])
        assert len(checks) > 0, "No structural checks were run"

    def test_verification_xbrl_status_without_arelle(self):
        from zion_terminal.pipeline.filing_pipeline import FilingPipeline
        pipeline = FilingPipeline()
        result = pipeline.process(
            html="<html><body><p>Test</p></body></html>",
            ticker="TEST",
            form="10-K",
        )
        # Without an XBRL URL, status should be no_xbrl_url
        assert result.verification.get("xbrl_status") == "no_xbrl_url"
