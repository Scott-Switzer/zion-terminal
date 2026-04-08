"""Synthesis Agent – generates semi-fictional companies with consistent financials.

Phase 2 component: creates internally-consistent financial statements,
SEC-style filings, press releases, and analyst notes.  When no LLM is
available, falls back to template-based generation with random variation.
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

from src.models.responses import SynthesisResult

logger = logging.getLogger(__name__)

# ── Templates ────────────────────────────────────────────────────────────

_SECTORS = [
    ("Technology", "Software", "SaaS"),
    ("Technology", "Semiconductors", "Chip Manufacturing"),
    ("Healthcare", "Biotechnology", "Drug Development"),
    ("Healthcare", "Medical Devices", "Surgical Equipment"),
    ("Finance", "Fintech", "Payment Processing"),
    ("Consumer", "E-commerce", "Online Retail"),
    ("Energy", "Renewable", "Solar Manufacturing"),
    ("Industrial", "Aerospace", "Defense Contracting"),
]

_NAME_PREFIXES = [
    "Nova", "Apex", "Zenith", "Vanguard", "Pinnacle", "Stellar",
    "Quantum", "Meridian", "Titan", "Nexus", "Vertex", "Horizon",
    "Catalyst", "Forge", "Prism", "Atlas", "Summit", "Ionic",
]

_NAME_SUFFIXES = [
    "Systems", "Technologies", "Corp", "Industries", "Dynamics",
    "Labs", "Holdings", "Solutions", "Sciences", "Networks",
    "Global", "Innovations", "Analytics", "Platforms", "Group",
]


def _random_ticker(name: str) -> str:
    """Generate a plausible ticker from a company name."""
    words = name.split()
    if len(words) >= 2:
        return (words[0][:2] + words[1][0]).upper()
    return name[:4].upper()


def _generate_revenue_trajectory(
    base: float, quarters: int = 8, growth_rate: float = 0.05
) -> list[float]:
    """Generate a plausible quarterly revenue series with seasonal variation."""
    revenues = []
    seasonal = [1.0, 0.95, 1.02, 1.10]  # Q1-Q4 seasonality
    for q in range(quarters):
        factor = (1 + growth_rate) ** q
        season = seasonal[q % 4]
        noise = random.uniform(0.97, 1.03)
        revenues.append(round(base * factor * season * noise, 2))
    return revenues


class SynthesisAgent:
    """Generate synthetic financial entities and documents."""

    def __init__(self, openai_client=None, model: str = "gpt-4o-mini") -> None:
        self._client = openai_client
        self._model = model

    def generate(self, query: str, params: dict[str, Any] | None = None) -> SynthesisResult:
        """Generate a synthetic entity based on the query."""
        params = params or {}

        try:
            # Generate company profile
            company = self._generate_company(params)
            ticker = company["ticker"]
            name = company["name"]

            # Generate financial statements
            income_stmt = self._generate_income_statement(company)
            balance_sheet = self._generate_balance_sheet(company, income_stmt)
            cash_flow = self._generate_cash_flow(company, income_stmt, balance_sheet)

            documents = [
                {"type": "company_profile", "data": company},
                {"type": "income_statement", "data": income_stmt},
                {"type": "balance_sheet", "data": balance_sheet},
                {"type": "cash_flow_statement", "data": cash_flow},
            ]

            # Optionally generate press release with LLM
            if self._client:
                press_release = self._generate_press_release(company, income_stmt)
                if press_release:
                    documents.append({"type": "press_release", "data": press_release})

            return SynthesisResult(
                success=True,
                documents=documents,
                entity_name=name,
                entity_ticker=ticker,
            )
        except Exception as exc:
            logger.exception("Synthesis failed")
            return SynthesisResult(
                success=False,
                errors=[f"Synthesis error: {exc}"],
            )

    def _generate_company(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a company profile."""
        sector, industry, sub = random.choice(_SECTORS)
        prefix = params.get("name_prefix") or random.choice(_NAME_PREFIXES)
        suffix = params.get("name_suffix") or random.choice(_NAME_SUFFIXES)
        name = f"{prefix} {suffix}"
        ticker = params.get("ticker") or _random_ticker(name)

        base_revenue = random.uniform(500_000_000, 50_000_000_000)
        employees = random.randint(500, 150_000)

        return {
            "name": name,
            "ticker": ticker,
            "sector": sector,
            "industry": industry,
            "sub_industry": sub,
            "founded": random.randint(1985, 2020),
            "headquarters": random.choice([
                "San Francisco, CA", "New York, NY", "Austin, TX",
                "Boston, MA", "Seattle, WA", "Denver, CO",
                "Chicago, IL", "Raleigh, NC",
            ]),
            "employees": employees,
            "base_revenue": base_revenue,
            "description": (
                f"{name} is a {industry.lower()} company in the "
                f"{sector.lower()} sector, specialising in {sub.lower()}."
            ),
        }

    def _generate_income_statement(self, company: dict) -> dict[str, Any]:
        """Generate a quarterly income statement."""
        revenue = company["base_revenue"] / 4
        cogs_pct = random.uniform(0.30, 0.65)
        sga_pct = random.uniform(0.10, 0.25)
        rnd_pct = random.uniform(0.05, 0.20)
        tax_rate = random.uniform(0.18, 0.25)
        interest = random.uniform(0, revenue * 0.03)

        cogs = revenue * cogs_pct
        gross_profit = revenue - cogs
        sga = revenue * sga_pct
        rnd = revenue * rnd_pct
        operating_income = gross_profit - sga - rnd
        ebt = operating_income - interest
        tax = max(0, ebt * tax_rate)
        net_income = ebt - tax

        return {
            "period": "Q4 2025",
            "total_revenue": round(revenue),
            "cost_of_revenue": round(cogs),
            "gross_profit": round(gross_profit),
            "selling_general_admin": round(sga),
            "research_development": round(rnd),
            "operating_income": round(operating_income),
            "interest_expense": round(interest),
            "income_before_tax": round(ebt),
            "income_tax": round(tax),
            "net_income": round(net_income),
        }

    def _generate_balance_sheet(
        self, company: dict, income: dict
    ) -> dict[str, Any]:
        """Generate a balance sheet consistent with the income statement."""
        net_income = income["net_income"]
        revenue = income["total_revenue"]

        # Assets
        cash = round(revenue * random.uniform(0.5, 2.0))
        receivables = round(revenue * random.uniform(0.10, 0.25))
        inventory = round(income["cost_of_revenue"] * random.uniform(0.15, 0.35))
        current_assets = cash + receivables + inventory
        ppe = round(revenue * random.uniform(1.0, 4.0))
        goodwill = round(revenue * random.uniform(0.2, 1.5))
        total_assets = current_assets + ppe + goodwill

        # Liabilities
        payables = round(income["cost_of_revenue"] * random.uniform(0.10, 0.25))
        short_debt = round(revenue * random.uniform(0.05, 0.20))
        current_liabilities = payables + short_debt
        long_debt = round(revenue * random.uniform(0.5, 2.5))
        total_liabilities = current_liabilities + long_debt

        # Equity (plug)
        total_equity = total_assets - total_liabilities
        retained_earnings = round(total_equity * random.uniform(0.3, 0.8))
        common_stock = total_equity - retained_earnings

        return {
            "period": "Q4 2025",
            "cash_and_equivalents": cash,
            "accounts_receivable": receivables,
            "inventory": inventory,
            "total_current_assets": current_assets,
            "property_plant_equipment": ppe,
            "goodwill": goodwill,
            "total_assets": total_assets,
            "accounts_payable": payables,
            "short_term_debt": short_debt,
            "total_current_liabilities": current_liabilities,
            "long_term_debt": long_debt,
            "total_liabilities": total_liabilities,
            "common_stock": common_stock,
            "retained_earnings": retained_earnings,
            "total_equity": total_equity,
            # Accounting identity
            "total_liabilities_and_equity": total_liabilities + total_equity,
        }

    def _generate_cash_flow(
        self, company: dict, income: dict, balance_sheet: dict
    ) -> dict[str, Any]:
        """Generate a cash flow statement reconciled to the balance sheet."""
        net_income = income["net_income"]

        # Operating
        depreciation = round(balance_sheet["property_plant_equipment"] * random.uniform(0.02, 0.05))
        wc_change = round(net_income * random.uniform(-0.15, 0.15))
        operating_cf = net_income + depreciation + wc_change

        # Investing
        capex = -round(balance_sheet["property_plant_equipment"] * random.uniform(0.03, 0.08))
        acquisitions = -round(random.uniform(0, balance_sheet["goodwill"] * 0.1))
        investing_cf = capex + acquisitions

        # Financing
        debt_change = round(random.uniform(-net_income * 0.3, net_income * 0.3))
        dividends = -round(max(0, net_income * random.uniform(0, 0.35)))
        buybacks = -round(max(0, net_income * random.uniform(0, 0.25)))
        financing_cf = debt_change + dividends + buybacks

        net_change = operating_cf + investing_cf + financing_cf
        beginning_cash = balance_sheet["cash_and_equivalents"] - net_change
        ending_cash = balance_sheet["cash_and_equivalents"]

        return {
            "period": "Q4 2025",
            "net_income": net_income,
            "depreciation_amortization": depreciation,
            "working_capital_change": wc_change,
            "operating_cash_flow": round(operating_cf),
            "capital_expenditures": capex,
            "acquisitions": acquisitions,
            "investing_cash_flow": round(investing_cf),
            "debt_issuance_repayment": debt_change,
            "dividends_paid": dividends,
            "share_buybacks": buybacks,
            "financing_cash_flow": round(financing_cf),
            "net_change_in_cash": round(net_change),
            "beginning_cash": round(beginning_cash),
            "ending_cash": ending_cash,
        }

    def _generate_press_release(
        self, company: dict, income: dict
    ) -> dict[str, Any] | None:
        """Use LLM to create a realistic earnings press release."""
        if not self._client:
            return None

        try:
            prompt = (
                f"Write a brief earnings press release for {company['name']} "
                f"(ticker: {company['ticker']}), a {company['industry']} company. "
                f"Q4 2025 results: Revenue ${income['total_revenue']:,.0f}, "
                f"Net income ${income['net_income']:,.0f}. "
                f"Keep it under 300 words. Professional tone. "
                f"Include forward-looking statements disclaimer."
            )

            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You write realistic corporate press releases."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=600,
            )

            return {
                "type": "press_release",
                "title": f"{company['name']} Reports Q4 2025 Results",
                "content": response.choices[0].message.content,
            }
        except Exception as exc:
            logger.warning("Press release generation failed: %s", exc)
            return None
