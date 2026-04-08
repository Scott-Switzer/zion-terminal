"""Synthesis Agent – generates semi-fictional companies with consistent financials.

Status: experimental (Phase 2).
Works without LLM: generates template-based financials.
With LLM: can generate press releases.
Validation is run on all output before serving.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from zion_terminal.models.responses import SynthesisResult
from zion_terminal.providers.base import BaseLLMProvider, NoLLMProvider

logger = logging.getLogger(__name__)

_SECTORS = [
    ("Technology", "Software"), ("Technology", "Semiconductors"),
    ("Healthcare", "Biotechnology"), ("Healthcare", "Medical Devices"),
    ("Finance", "Fintech"), ("Consumer", "E-commerce"),
    ("Energy", "Renewable"), ("Industrial", "Aerospace"),
]

_NAME_PREFIXES = ["Nova", "Apex", "Zenith", "Vanguard", "Pinnacle", "Stellar",
                  "Quantum", "Meridian", "Titan", "Nexus", "Vertex", "Horizon"]
_NAME_SUFFIXES = ["Systems", "Technologies", "Corp", "Industries", "Dynamics",
                  "Labs", "Holdings", "Solutions", "Sciences", "Networks"]


def _random_ticker(name: str) -> str:
    words = name.split()
    return (words[0][:2] + words[1][0]).upper() if len(words) >= 2 else name[:4].upper()


class SynthesisAgent:
    def __init__(self, llm: BaseLLMProvider | None = None) -> None:
        self._llm = llm or NoLLMProvider()
        self._rng = random.Random()  # Instance-level RNG for determinism

    def generate(self, query: str, params: dict[str, Any] | None = None) -> SynthesisResult:
        params = params or {}
        seed = params.get("seed")
        if seed is not None:
            self._rng = random.Random(seed)
        else:
            # Default deterministic seed from query for reproducibility
            self._rng = random.Random(hash(query) & 0xFFFFFFFF)
        try:
            company = self._generate_company(params)
            income_stmt = self._generate_income_statement(company)
            balance_sheet = self._generate_balance_sheet(company, income_stmt)
            cash_flow = self._generate_cash_flow(company, income_stmt, balance_sheet)

            documents = [
                {"type": "company_profile", "data": company},
                {"type": "income_statement", "data": income_stmt},
                {"type": "balance_sheet", "data": balance_sheet},
                {"type": "cash_flow_statement", "data": cash_flow},
            ]

            if self._llm.is_available and not isinstance(self._llm, NoLLMProvider):
                pr = self._generate_press_release(company, income_stmt)
                if pr:
                    documents.append({"type": "press_release", "data": pr})

            return SynthesisResult(
                success=True, documents=documents,
                entity_name=company["name"], entity_ticker=company["ticker"],
            )
        except Exception as exc:
            logger.exception("Synthesis failed")
            return SynthesisResult(success=False, errors=[f"Synthesis error: {exc}"])

    def generate_with_retry(
        self, query: str, params: dict[str, Any] | None = None,
        max_retries: int = 3,
        validator=None,
    ) -> SynthesisResult:
        """Generate with retry on validation failure (strict mode scaffold).

        If a validator is provided and the result fails validation,
        retries with a perturbed seed up to max_retries times.
        This is a scaffold — the validation integration is partial.

        Args:
            query: Generation query
            params: Generation parameters (including optional seed)
            max_retries: Maximum retry attempts
            validator: Optional ValidationAgent instance
        """
        params = params or {}
        base_seed = params.get("seed", hash(query) & 0xFFFFFFFF)

        for attempt in range(max_retries):
            params["seed"] = base_seed + attempt
            result = self.generate(query, params)

            if not result.success:
                continue

            if validator is None:
                return result

            validation = validator.validate_synthesis(result)
            if validation.checks_failed == 0:
                return result

            logger.warning(
                "Synthesis attempt %d/%d failed validation (%d checks failed), retrying",
                attempt + 1, max_retries, validation.checks_failed,
            )

        # All retries exhausted
        return SynthesisResult(
            success=False,
            errors=[f"Synthesis failed validation after {max_retries} attempts"],
        )

    def _generate_company(self, params: dict[str, Any]) -> dict[str, Any]:
        sector, industry = self._rng.choice(_SECTORS)
        name = f"{params.get('name_prefix') or self._rng.choice(_NAME_PREFIXES)} {params.get('name_suffix') or self._rng.choice(_NAME_SUFFIXES)}"
        ticker = params.get("ticker") or _random_ticker(name)
        return {
            "name": name, "ticker": ticker, "sector": sector, "industry": industry,
            "founded": self._rng.randint(1985, 2020),
            "headquarters": self._rng.choice(["San Francisco, CA", "New York, NY", "Austin, TX", "Boston, MA", "Seattle, WA"]),
            "employees": self._rng.randint(500, 150_000),
            "base_revenue": self._rng.uniform(500_000_000, 50_000_000_000),
            "description": f"{name} is a {industry.lower()} company in the {sector.lower()} sector.",
        }

    def _generate_income_statement(self, company: dict) -> dict[str, Any]:
        revenue_raw = company["base_revenue"] / 4
        cogs_pct = self._rng.uniform(0.30, 0.65)
        sga_pct = self._rng.uniform(0.10, 0.25)
        rnd_pct = self._rng.uniform(0.05, 0.20)
        tax_rate = self._rng.uniform(0.18, 0.25)

        # Round first, then derive — ensures exact accounting identities
        revenue = round(revenue_raw)
        cogs = round(revenue_raw * cogs_pct)
        gross_profit = revenue - cogs  # exact
        sga = round(revenue_raw * sga_pct)
        rnd = round(revenue_raw * rnd_pct)
        operating_income = gross_profit - sga - rnd  # exact
        interest = round(self._rng.uniform(0, revenue_raw * 0.03))
        ebt = operating_income - interest  # exact
        tax = round(max(0, ebt * tax_rate))
        net_income = ebt - tax  # exact

        return {
            "period": "Q4 2025", "total_revenue": revenue,
            "cost_of_revenue": cogs, "gross_profit": gross_profit,
            "selling_general_admin": sga,
            "research_development": rnd,
            "operating_income": operating_income, "interest_expense": interest,
            "income_before_tax": ebt, "income_tax": tax,
            "net_income": net_income,
        }

    def _generate_balance_sheet(self, company: dict, income: dict) -> dict[str, Any]:
        revenue = income["total_revenue"]
        cash = round(revenue * self._rng.uniform(0.5, 2.0))
        receivables = round(revenue * self._rng.uniform(0.10, 0.25))
        inventory = round(income["cost_of_revenue"] * self._rng.uniform(0.15, 0.35))
        current_assets = cash + receivables + inventory
        ppe = round(revenue * self._rng.uniform(1.0, 4.0))
        goodwill = round(revenue * self._rng.uniform(0.2, 1.5))
        total_assets = current_assets + ppe + goodwill
        payables = round(income["cost_of_revenue"] * self._rng.uniform(0.10, 0.25))
        short_debt = round(revenue * self._rng.uniform(0.05, 0.20))
        current_liabilities = payables + short_debt
        long_debt = round(revenue * self._rng.uniform(0.5, 2.5))
        total_liabilities = current_liabilities + long_debt
        total_equity = total_assets - total_liabilities
        retained_earnings = round(total_equity * self._rng.uniform(0.3, 0.8))
        return {
            "period": "Q4 2025",
            "cash_and_equivalents": cash, "accounts_receivable": receivables,
            "inventory": inventory, "total_current_assets": current_assets,
            "property_plant_equipment": ppe, "goodwill": goodwill,
            "total_assets": total_assets, "accounts_payable": payables,
            "short_term_debt": short_debt, "total_current_liabilities": current_liabilities,
            "long_term_debt": long_debt, "total_liabilities": total_liabilities,
            "common_stock": total_equity - retained_earnings,
            "retained_earnings": retained_earnings, "total_equity": total_equity,
            "total_liabilities_and_equity": total_liabilities + total_equity,
        }

    def _generate_cash_flow(self, company: dict, income: dict, bs: dict) -> dict[str, Any]:
        ni = income["net_income"]
        depreciation = round(bs["property_plant_equipment"] * self._rng.uniform(0.02, 0.05))
        wc_change = round(ni * self._rng.uniform(-0.15, 0.15))
        op_cf = ni + depreciation + wc_change
        capex = -round(bs["property_plant_equipment"] * self._rng.uniform(0.03, 0.08))
        acq = -round(self._rng.uniform(0, bs["goodwill"] * 0.1))
        inv_cf = capex + acq
        debt_chg = round(self._rng.uniform(-ni * 0.3, ni * 0.3))
        dividends = -round(max(0, ni * self._rng.uniform(0, 0.35)))
        buybacks = -round(max(0, ni * self._rng.uniform(0, 0.25)))
        fin_cf = debt_chg + dividends + buybacks
        net_change = op_cf + inv_cf + fin_cf
        ending_cash = bs["cash_and_equivalents"]
        return {
            "period": "Q4 2025", "net_income": ni,
            "depreciation_amortization": depreciation, "working_capital_change": wc_change,
            "operating_cash_flow": round(op_cf),
            "capital_expenditures": capex, "acquisitions": acq,
            "investing_cash_flow": round(inv_cf),
            "debt_issuance_repayment": debt_chg, "dividends_paid": dividends,
            "share_buybacks": buybacks, "financing_cash_flow": round(fin_cf),
            "net_change_in_cash": round(net_change),
            "beginning_cash": round(ending_cash - net_change),
            "ending_cash": ending_cash,
        }

    def _generate_press_release(self, company: dict, income: dict) -> dict[str, Any] | None:
        content = self._llm.complete(
            system="You write realistic corporate press releases.",
            user=(
                f"Write a brief earnings press release for {company['name']} "
                f"(ticker: {company['ticker']}), a {company['industry']} company. "
                f"Q4 2025 results: Revenue ${income['total_revenue']:,.0f}, "
                f"Net income ${income['net_income']:,.0f}. "
                f"Keep it under 300 words. Professional tone."
            ),
            temperature=0.7, max_tokens=600,
        )
        if content:
            return {"type": "press_release", "title": f"{company['name']} Reports Q4 2025 Results", "content": content}
        return None
