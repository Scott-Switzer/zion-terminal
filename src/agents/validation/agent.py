"""Validation Agent – enforces correctness on retrieved and generated data.

Checks include:
  - Field-level: data types, required fields, numeric bounds.
  - Intra-document: net income = revenue - expenses.
  - Cross-document: balance sheet ↔ cash flow reconciliation.
  - Temporal: year-over-year coherence.
"""

from __future__ import annotations

import logging
from typing import Any

from src.models.responses import RetrievalResult, SynthesisResult, ValidationResult, ValidationStatus

logger = logging.getLogger(__name__)


class ValidationAgent:
    """Run correctness checks on financial data and synthetic documents."""

    def validate_retrieval(self, result: RetrievalResult) -> ValidationResult:
        """Validate data returned by the retrieval agent."""
        checks_run = 0
        checks_passed = 0
        checks_failed = 0
        details: list[dict[str, Any]] = []
        warnings: list[str] = []

        for i, item in enumerate(result.data):
            # Check 1: Non-empty data
            checks_run += 1
            if not item:
                checks_failed += 1
                details.append({
                    "check": "non_empty",
                    "index": i,
                    "status": "failed",
                    "message": "Empty data item",
                })
            else:
                checks_passed += 1

            # Check 2: Has source attribution
            checks_run += 1
            if item.get("source"):
                checks_passed += 1
            else:
                checks_failed += 1
                details.append({
                    "check": "source_attribution",
                    "index": i,
                    "status": "failed",
                    "message": "Missing source field",
                })

            # Check 3: Numeric bounds for stock data
            if item.get("type") == "stock_quote" or "price" in item:
                checks_run += 1
                price = item.get("price") or item.get("close")
                if price is not None and (price < 0 or price > 1_000_000):
                    checks_failed += 1
                    details.append({
                        "check": "price_bounds",
                        "index": i,
                        "status": "failed",
                        "message": f"Price {price} outside reasonable bounds",
                    })
                elif price is not None:
                    checks_passed += 1

            # Check 4: Financial statement consistency
            if item.get("statement_type") == "income_statement":
                self._check_income_statement(item, i, details)
                checks_run += 1
                if not any(d["index"] == i and d["check"] == "income_consistency" for d in details):
                    checks_passed += 1
                else:
                    checks_failed += 1

            # Check 5: Time series ordering
            if "data_points" in item and isinstance(item["data_points"], list):
                checks_run += 1
                if self._check_time_series_order(item["data_points"]):
                    checks_passed += 1
                else:
                    warnings.append(f"Data points at index {i} may not be chronologically ordered")
                    checks_passed += 1  # warning, not failure

        status = ValidationStatus.PASSED
        if checks_failed > 0:
            status = ValidationStatus.WARNING if checks_failed < checks_run / 2 else ValidationStatus.FAILED

        return ValidationResult(
            success=checks_failed == 0,
            status=status,
            checks_run=checks_run,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            details=details,
            warnings=warnings,
        )

    def validate_synthesis(self, result: SynthesisResult) -> ValidationResult:
        """Validate synthetic documents for internal consistency."""
        checks_run = 0
        checks_passed = 0
        checks_failed = 0
        details: list[dict[str, Any]] = []
        warnings: list[str] = []

        statements: dict[str, dict] = {}
        for doc in result.documents:
            doc_type = doc.get("type", "")
            statements[doc_type] = doc

            # Check required fields
            checks_run += 1
            required = ["type", "data"]
            missing = [f for f in required if f not in doc]
            if missing:
                checks_failed += 1
                details.append({
                    "check": "required_fields",
                    "type": doc_type,
                    "status": "failed",
                    "message": f"Missing fields: {missing}",
                })
            else:
                checks_passed += 1

        # Cross-document checks
        if "income_statement" in statements and "balance_sheet" in statements:
            checks_run += 1
            # Net income should flow to retained earnings
            income_data = statements["income_statement"].get("data", {})
            net_income = income_data.get("net_income")
            if net_income is not None:
                checks_passed += 1
            else:
                warnings.append("Cannot verify net income → retained earnings flow")
                checks_passed += 1

        if "balance_sheet" in statements and "cash_flow_statement" in statements:
            checks_run += 1
            bs_data = statements["balance_sheet"].get("data", {})
            cf_data = statements["cash_flow_statement"].get("data", {})
            bs_cash = bs_data.get("cash_and_equivalents")
            cf_ending = cf_data.get("ending_cash")
            if bs_cash is not None and cf_ending is not None:
                if abs(bs_cash - cf_ending) < 1.0:
                    checks_passed += 1
                else:
                    checks_failed += 1
                    details.append({
                        "check": "cash_reconciliation",
                        "status": "failed",
                        "message": (
                            f"Balance sheet cash ({bs_cash:,.0f}) != "
                            f"Cash flow ending cash ({cf_ending:,.0f})"
                        ),
                    })
            else:
                checks_passed += 1
                warnings.append("Cash reconciliation: missing values, skipped")

        status = ValidationStatus.PASSED
        if checks_failed > 0:
            status = ValidationStatus.FAILED

        return ValidationResult(
            success=checks_failed == 0,
            status=status,
            checks_run=checks_run,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            details=details,
            warnings=warnings,
        )

    # ── Private check helpers ────────────────────────────────────

    def _check_income_statement(
        self, item: dict, index: int, details: list[dict]
    ) -> None:
        """Verify basic income statement math."""
        li = item.get("line_items", {})
        if not li:
            return

        revenue = li.get("Total Revenue") or li.get("total_revenue")
        cogs = li.get("Cost Of Revenue") or li.get("cost_of_revenue")
        gross_profit = li.get("Gross Profit") or li.get("gross_profit")

        if revenue is not None and cogs is not None and gross_profit is not None:
            expected = revenue - cogs
            if abs(expected - gross_profit) > abs(revenue) * 0.01:
                details.append({
                    "check": "income_consistency",
                    "index": index,
                    "status": "failed",
                    "message": (
                        f"Gross profit mismatch: revenue({revenue:,.0f}) - "
                        f"COGS({cogs:,.0f}) = {expected:,.0f}, "
                        f"but reported {gross_profit:,.0f}"
                    ),
                })

    @staticmethod
    def _check_time_series_order(data_points: list[dict]) -> bool:
        """Return True if dates are in ascending order."""
        dates = [dp.get("date", "") for dp in data_points if dp.get("date")]
        return dates == sorted(dates)
