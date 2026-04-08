"""Tests for the synthesis agent."""

from src.agents.synthesis.agent import SynthesisAgent


class TestSynthesisAgent:
    def setup_method(self):
        self.agent = SynthesisAgent(openai_client=None)

    def test_generate_creates_documents(self):
        result = self.agent.generate("Generate a synthetic company")
        assert result.success
        assert result.entity_name is not None
        assert result.entity_ticker is not None
        assert len(result.documents) >= 4  # profile, income, balance, cash flow

    def test_generate_has_all_statement_types(self):
        result = self.agent.generate("Create a company")
        types = {doc["type"] for doc in result.documents}
        assert "company_profile" in types
        assert "income_statement" in types
        assert "balance_sheet" in types
        assert "cash_flow_statement" in types

    def test_income_statement_math(self):
        result = self.agent.generate("Generate company")
        income = next(d for d in result.documents if d["type"] == "income_statement")
        data = income["data"]

        # Gross profit = revenue - COGS
        expected_gp = data["total_revenue"] - data["cost_of_revenue"]
        assert abs(data["gross_profit"] - expected_gp) < 2  # rounding tolerance

        # Net income makes sense
        assert data["net_income"] < data["total_revenue"]

    def test_balance_sheet_identity(self):
        result = self.agent.generate("Generate company")
        bs = next(d for d in result.documents if d["type"] == "balance_sheet")
        data = bs["data"]

        # Assets = Liabilities + Equity
        assert abs(data["total_assets"] - data["total_liabilities_and_equity"]) < 2

    def test_cash_flow_reconciliation(self):
        result = self.agent.generate("Generate company")
        cf = next(d for d in result.documents if d["type"] == "cash_flow_statement")
        bs = next(d for d in result.documents if d["type"] == "balance_sheet")
        cf_data = cf["data"]
        bs_data = bs["data"]

        # Ending cash should match balance sheet cash
        assert cf_data["ending_cash"] == bs_data["cash_and_equivalents"]

    def test_company_profile_fields(self):
        result = self.agent.generate("Generate")
        profile = next(d for d in result.documents if d["type"] == "company_profile")
        data = profile["data"]
        assert "name" in data
        assert "ticker" in data
        assert "sector" in data
        assert "industry" in data
        assert "employees" in data
