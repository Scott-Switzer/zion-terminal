"""Validation Agent – enforces correctness on retrieved and generated data.

All checks produce machine-readable ValidationCheck objects.
"""

from __future__ import annotations

import logging
from typing import Any

from zion_terminal.models.responses import (
    RetrievalResult, SynthesisResult, ValidationCheck, ValidationResult, ValidationStatus,
)

logger = logging.getLogger(__name__)


class ValidationAgent:
    def validate_retrieval(self, result: RetrievalResult) -> ValidationResult:
        checks: list[ValidationCheck] = []

        for i, item in enumerate(result.data):
            prefix = f"item[{i}]"

            # Check: non-empty
            if not item:
                checks.append(ValidationCheck(
                    check_name="non_empty", status=ValidationStatus.FAILED,
                    message=f"{prefix}: empty data item", field=prefix,
                ))
                continue
            else:
                checks.append(ValidationCheck(
                    check_name="non_empty", status=ValidationStatus.PASSED,
                    message=f"{prefix}: data present", field=prefix,
                ))

            # Check: source attribution
            if item.get("source"):
                checks.append(ValidationCheck(
                    check_name="source_attribution", status=ValidationStatus.PASSED,
                    message=f"{prefix}: source={item['source']}", field=f"{prefix}.source",
                ))
            else:
                checks.append(ValidationCheck(
                    check_name="source_attribution", status=ValidationStatus.FAILED,
                    message=f"{prefix}: missing source field", field=f"{prefix}.source",
                ))

            # Check: price bounds for stock data
            price = item.get("price") or item.get("close")
            if price is not None:
                if isinstance(price, (int, float)) and 0 < price < 1_000_000:
                    checks.append(ValidationCheck(
                        check_name="price_bounds", status=ValidationStatus.PASSED,
                        message=f"{prefix}: price={price} within bounds", field=f"{prefix}.price",
                    ))
                else:
                    checks.append(ValidationCheck(
                        check_name="price_bounds", status=ValidationStatus.FAILED,
                        message=f"{prefix}: price={price} outside [0, 1M]",
                        field=f"{prefix}.price", expected="0 < price < 1,000,000", actual=price,
                    ))

            # Check: volume non-negative
            volume = item.get("volume")
            if volume is not None and isinstance(volume, (int, float)):
                if volume >= 0:
                    checks.append(ValidationCheck(
                        check_name="volume_non_negative", status=ValidationStatus.PASSED,
                        message=f"{prefix}: volume={volume}", field=f"{prefix}.volume",
                    ))
                else:
                    checks.append(ValidationCheck(
                        check_name="volume_non_negative", status=ValidationStatus.FAILED,
                        message=f"{prefix}: negative volume={volume}", field=f"{prefix}.volume",
                        expected=">=0", actual=volume,
                    ))

            # Check: income statement math
            if item.get("statement_type") == "income_statement":
                self._check_income_math(item, prefix, checks)

            # Check: time series chronological order
            if "data_points" in item and isinstance(item["data_points"], list) and len(item["data_points"]) > 1:
                dates = [dp.get("date", "") for dp in item["data_points"] if dp.get("date")]
                if dates == sorted(dates):
                    checks.append(ValidationCheck(
                        check_name="chronological_order", status=ValidationStatus.PASSED,
                        message=f"{prefix}: {len(dates)} points in order", field=f"{prefix}.data_points",
                    ))
                else:
                    checks.append(ValidationCheck(
                        check_name="chronological_order", status=ValidationStatus.WARNING,
                        message=f"{prefix}: data points not chronologically sorted",
                        field=f"{prefix}.data_points",
                    ))

            # Check: schema presence for known types
            data_type = item.get("type") or item.get("statement_type")
            if data_type:
                checks.append(ValidationCheck(
                    check_name="has_type_field", status=ValidationStatus.PASSED,
                    message=f"{prefix}: type={data_type}", field=f"{prefix}.type",
                ))

        return self._build_result(checks)

    def validate_synthesis(self, result: SynthesisResult) -> ValidationResult:
        checks: list[ValidationCheck] = []
        statements: dict[str, dict] = {}

        for doc in result.documents:
            doc_type = doc.get("type", "unknown")
            data = doc.get("data", {})
            statements[doc_type] = data

            # Required fields
            required = ["type", "data"]
            missing = [f for f in required if f not in doc]
            if missing:
                checks.append(ValidationCheck(
                    check_name="required_fields", status=ValidationStatus.FAILED,
                    message=f"{doc_type}: missing {missing}", field=doc_type,
                ))
            else:
                checks.append(ValidationCheck(
                    check_name="required_fields", status=ValidationStatus.PASSED,
                    message=f"{doc_type}: all required fields present", field=doc_type,
                ))

            # Income statement math
            if doc_type == "income_statement" and isinstance(data, dict):
                revenue = data.get("total_revenue")
                cogs = data.get("cost_of_revenue")
                gp = data.get("gross_profit")
                if all(v is not None for v in [revenue, cogs, gp]):
                    expected = revenue - cogs
                    if abs(expected - gp) < 2:
                        checks.append(ValidationCheck(
                            check_name="gross_profit_math", status=ValidationStatus.PASSED,
                            message="revenue - COGS = gross profit", field="income_statement",
                        ))
                    else:
                        checks.append(ValidationCheck(
                            check_name="gross_profit_math", status=ValidationStatus.FAILED,
                            message=f"revenue({revenue:,.0f}) - COGS({cogs:,.0f}) = {expected:,.0f}, got {gp:,.0f}",
                            field="income_statement", expected=expected, actual=gp,
                        ))

        # Cross-document: balance sheet identity
        bs = statements.get("balance_sheet", {})
        if bs.get("total_assets") is not None and bs.get("total_liabilities_and_equity") is not None:
            if abs(bs["total_assets"] - bs["total_liabilities_and_equity"]) < 2:
                checks.append(ValidationCheck(
                    check_name="accounting_identity", status=ValidationStatus.PASSED,
                    message="Assets = Liabilities + Equity", field="balance_sheet",
                ))
            else:
                checks.append(ValidationCheck(
                    check_name="accounting_identity", status=ValidationStatus.FAILED,
                    message=f"Assets({bs['total_assets']:,.0f}) != L+E({bs['total_liabilities_and_equity']:,.0f})",
                    field="balance_sheet",
                    expected=bs["total_assets"], actual=bs["total_liabilities_and_equity"],
                ))

        # Cross-document: cash reconciliation
        cf = statements.get("cash_flow_statement", {})
        if bs.get("cash_and_equivalents") is not None and cf.get("ending_cash") is not None:
            if abs(bs["cash_and_equivalents"] - cf["ending_cash"]) < 2:
                checks.append(ValidationCheck(
                    check_name="cash_reconciliation", status=ValidationStatus.PASSED,
                    message="BS cash = CF ending cash", field="cross_document",
                ))
            else:
                checks.append(ValidationCheck(
                    check_name="cash_reconciliation", status=ValidationStatus.FAILED,
                    message=f"BS cash({bs['cash_and_equivalents']:,.0f}) != CF ending({cf['ending_cash']:,.0f})",
                    field="cross_document",
                    expected=bs["cash_and_equivalents"], actual=cf["ending_cash"],
                ))

        return self._build_result(checks)

    def _check_income_math(self, item: dict, prefix: str, checks: list[ValidationCheck]) -> None:
        li = item.get("line_items", {})
        if not li:
            return
        revenue = li.get("Total Revenue") or li.get("total_revenue")
        cogs = li.get("Cost Of Revenue") or li.get("cost_of_revenue")
        gp = li.get("Gross Profit") or li.get("gross_profit")
        if all(v is not None for v in [revenue, cogs, gp]):
            expected = revenue - cogs
            tol = abs(revenue) * 0.01
            if abs(expected - gp) <= tol:
                checks.append(ValidationCheck(
                    check_name="income_math", status=ValidationStatus.PASSED,
                    message=f"{prefix}: gross profit consistent", field=f"{prefix}.line_items",
                ))
            else:
                checks.append(ValidationCheck(
                    check_name="income_math", status=ValidationStatus.WARNING,
                    message=f"{prefix}: gross profit mismatch ({expected:,.0f} vs {gp:,.0f})",
                    field=f"{prefix}.line_items", expected=expected, actual=gp,
                ))

    @staticmethod
    def _build_result(checks: list[ValidationCheck]) -> ValidationResult:
        passed = sum(1 for c in checks if c.status == ValidationStatus.PASSED)
        failed = sum(1 for c in checks if c.status == ValidationStatus.FAILED)
        status = ValidationStatus.PASSED
        if failed > 0:
            status = ValidationStatus.WARNING if failed < len(checks) / 2 else ValidationStatus.FAILED
        return ValidationResult(
            success=failed == 0,
            status=status,
            checks_run=len(checks),
            checks_passed=passed,
            checks_failed=failed,
            details=checks,
            warnings=[c.message for c in checks if c.status == ValidationStatus.WARNING],
        )
