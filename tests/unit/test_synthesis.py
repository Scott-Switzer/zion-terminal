"""Tests for the synthesis agent."""

import pytest

from zion_terminal.agents.synthesis.agent import SynthesisAgent
from zion_terminal.providers.base import NoLLMProvider


class TestSynthesisAgent:
    def setup_method(self):
        self.agent = SynthesisAgent(llm=NoLLMProvider())

    def test_generates_company_profile(self):
        result = self.agent.generate("generate a company")
        assert result.success is True
        types = [d["type"] for d in result.documents]
        assert "company_profile" in types

    def test_generates_all_statements(self):
        result = self.agent.generate("generate")
        types = [d["type"] for d in result.documents]
        assert "income_statement" in types
        assert "balance_sheet" in types
        assert "cash_flow_statement" in types

    def test_no_press_release_without_llm(self):
        """Without LLM, no press release should be generated."""
        result = self.agent.generate("generate")
        types = [d["type"] for d in result.documents]
        assert "press_release" not in types

    def test_income_statement_math(self):
        """revenue - COGS should equal gross profit."""
        result = self.agent.generate("generate")
        income = None
        for doc in result.documents:
            if doc["type"] == "income_statement":
                income = doc["data"]
                break
        assert income is not None
        assert income["total_revenue"] - income["cost_of_revenue"] == income["gross_profit"]

    def test_balance_sheet_identity(self):
        """total_assets should equal total_liabilities + total_equity."""
        result = self.agent.generate("generate")
        bs = None
        for doc in result.documents:
            if doc["type"] == "balance_sheet":
                bs = doc["data"]
                break
        assert bs is not None
        assert bs["total_assets"] == bs["total_liabilities_and_equity"]

    def test_entity_name_set(self):
        result = self.agent.generate("generate")
        assert result.entity_name is not None
        assert len(result.entity_name) > 0

    def test_entity_ticker_set(self):
        result = self.agent.generate("generate")
        assert result.entity_ticker is not None
        assert len(result.entity_ticker) >= 2

    def test_cash_reconciliation(self):
        """BS cash should equal CF ending cash."""
        result = self.agent.generate("generate")
        bs_cash = None
        cf_ending = None
        for doc in result.documents:
            if doc["type"] == "balance_sheet":
                bs_cash = doc["data"]["cash_and_equivalents"]
            elif doc["type"] == "cash_flow_statement":
                cf_ending = doc["data"]["ending_cash"]
        assert bs_cash is not None
        assert cf_ending is not None
        assert bs_cash == cf_ending
