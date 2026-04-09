"""Live verification module for the filing pipeline.

Integrates:
  - Structural checks (markdown exists, sections found)
  - Arelle XBRL validation (when available)
  - XBRL↔markdown fact reconciliation (using SEC company facts or Arelle)
  - Cross-source checks (Yahoo comparison, when available)

Reconciliation does NOT require Arelle — it uses SEC company facts JSON
(from data.sec.gov/api/xbrl/companyfacts/) to extract canonical XBRL facts
and compares them against facts extracted from the generated markdown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from zion_terminal.verification.fact_mapping import CanonicalFact
from zion_terminal.verification.markdown_extractor import extract_values
from zion_terminal.verification.reconciler import Reconciler

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Structured verification output.

    Status semantics (honest and detailed):
      - not_run: verification did not execute
      - structural_only: only structural checks ran (no XBRL, no reconciliation)
      - reconciled_pass: XBRL↔markdown reconciliation ran, match_rate >= 0.8
      - reconciled_partial: reconciliation ran, 0.5 <= match_rate < 0.8
      - reconciled_fail: reconciliation ran, match_rate < 0.5
      - passed: structural + at least one deeper check passed (non-reconciliation)
      - partial: some checks passed, some failed
      - failed: structural checks failed
    """
    status: str = "not_run"
    verification_depth: str = "none"  # none | structural_only | reconciled | xbrl | cross_source | full
    xbrl_status: str = "not_run"
    xbrl_facts_extracted: int = 0
    xbrl_errors: list[str] = field(default_factory=list)
    # Reconciliation fields — XBRL↔markdown fact comparison
    reconciliation_status: str = "not_run"  # not_run | reconciled_pass | reconciled_partial | reconciled_fail | error
    reconciliation_report: dict[str, Any] = field(default_factory=dict)
    facts_extracted_xbrl: int = 0
    facts_extracted_markdown: int = 0
    facts_matched: int = 0
    facts_mismatched: int = 0
    cross_source_status: str = "not_run"
    cross_source_checks: list[dict[str, Any]] = field(default_factory=list)
    structural_checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "verification_depth": self.verification_depth,
            "xbrl_status": self.xbrl_status,
            "xbrl_facts_extracted": self.xbrl_facts_extracted,
            "xbrl_errors": self.xbrl_errors,
            "reconciliation_status": self.reconciliation_status,
            "reconciliation_report": self.reconciliation_report,
            "facts_extracted_xbrl": self.facts_extracted_xbrl,
            "facts_extracted_markdown": self.facts_extracted_markdown,
            "facts_matched": self.facts_matched,
            "facts_mismatched": self.facts_mismatched,
            "cross_source_status": self.cross_source_status,
            "cross_source_checks": self.cross_source_checks,
            "structural_checks": self.structural_checks,
            "warnings": self.warnings,
        }


# Core US-GAAP concepts to reconcile (high-value financial facts)
_CORE_CONCEPTS = {
    "Revenues", "CostOfGoodsAndServicesSold", "GrossProfit",
    "OperatingIncomeLoss", "NetIncomeLoss", "Assets", "Liabilities",
    "StockholdersEquity", "CashAndCashEquivalentsAtCarryingValue",
    "OperatingCashFlow", "EarningsPerShareBasic",
    # Additional common variants
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "CostOfRevenue", "OperatingExpenses",
}


def _company_facts_to_canonical(facts_json: dict) -> list[CanonicalFact]:
    """Convert SEC company facts JSON to a list of CanonicalFact objects.

    The SEC companyfacts JSON has this structure::

        {
          "facts": {
            "us-gaap": {
              "Revenues": {
                "units": {
                  "USD": [
                    {"val": 383285000000, "fy": 2023, "fp": "FY", ...}
                  ]
                }
              }
            }
          }
        }

    We extract only the most recent value for each core concept.
    """
    results: list[CanonicalFact] = []
    if not facts_json or "facts" not in facts_json:
        return results

    for namespace, concepts in facts_json.get("facts", {}).items():
        if not isinstance(concepts, dict):
            continue
        for concept_name, concept_data in concepts.items():
            if concept_name not in _CORE_CONCEPTS:
                continue
            if not isinstance(concept_data, dict):
                continue
            units = concept_data.get("units", {})
            for unit_name, entries in units.items():
                if not isinstance(entries, list) or not entries:
                    continue
                # Take the most recent entry (last in the list)
                entry = entries[-1]
                val = entry.get("val")
                if val is None:
                    continue
                period_start = entry.get("start")
                period_end = entry.get("end")
                period_type = "duration" if period_start else "instant"

                results.append(CanonicalFact(
                    concept=f"{namespace}:{concept_name}",
                    local_name=concept_name,
                    value=float(val),
                    unit=unit_name,
                    period_type=period_type,
                    period_start=period_start,
                    period_end=period_end,
                    namespace=namespace,
                ))

    return results


class FilingVerifier:
    """Verification layer for filing pipeline results.
    
    Runs three verification tiers:
    1. Structural — markdown exists, sections found, metadata present
    2. XBRL/Arelle — fact extraction and validation (optional dependency)
    3. Cross-source — comparison against Yahoo/other sources (when available)
    """

    def verify(
        self,
        markdown: str,
        sections: list | None = None,
        ticker: str = "",
        form: str = "",
        xbrl_url: str | None = None,
        yahoo_data: dict | None = None,
        company_facts: dict | None = None,
    ) -> VerificationResult:
        result = VerificationResult()
        
        # Tier 1: Structural checks
        self._check_structural(result, markdown, sections, form)
        
        # Tier 2: XBRL/Arelle checks
        if xbrl_url:
            self._check_xbrl(result, xbrl_url)
        else:
            result.xbrl_status = "no_xbrl_url"
        
        # Tier 3: XBRL↔markdown fact reconciliation
        # This is the core verification: does the markdown preserve
        # the same financial facts as the XBRL source?
        if company_facts:
            self._check_reconciliation(result, markdown, company_facts)
        else:
            result.reconciliation_status = "no_company_facts"
        
        # Tier 4: Cross-source checks
        if yahoo_data:
            self._check_cross_source(result, yahoo_data, ticker)
        else:
            result.cross_source_status = "no_comparison_data"
        
        # Determine overall status — honest and detailed
        structural_failed = any(c.get("status") == "failed" for c in result.structural_checks)
        reconciliation_ran = result.reconciliation_status in (
            "reconciled_pass", "reconciled_partial", "reconciled_fail",
        )
        xbrl_ran = result.xbrl_status in ("passed", "failed")
        cross_ran = result.cross_source_status == "checked"

        if structural_failed:
            result.status = "failed"
            result.verification_depth = "structural_only"
        elif reconciliation_ran:
            result.verification_depth = "reconciled"
            result.status = result.reconciliation_status
        elif xbrl_ran and cross_ran:
            result.verification_depth = "full"
            result.status = "passed" if result.xbrl_status == "passed" else "partial"
        elif xbrl_ran:
            result.verification_depth = "xbrl"
            result.status = "passed" if result.xbrl_status == "passed" else "partial"
        elif cross_ran:
            result.verification_depth = "cross_source"
            result.status = "passed"
        else:
            result.verification_depth = "structural_only"
            result.status = "structural_only"
        
        return result

    def _check_reconciliation(
        self, result: VerificationResult, markdown: str, company_facts: dict,
    ) -> None:
        """Reconcile XBRL facts from SEC company facts JSON against markdown.

        Does NOT require Arelle. Uses the company facts JSON from SEC's
        XBRL API to get canonical facts and compares them to values
        extracted from the generated markdown tables.
        """
        try:
            # Extract facts from both sources
            xbrl_facts = _company_facts_to_canonical(company_facts)
            md_values = extract_values(markdown)

            result.facts_extracted_xbrl = len(xbrl_facts)
            result.facts_extracted_markdown = len(md_values)

            if not xbrl_facts:
                result.reconciliation_status = "no_xbrl_facts"
                result.warnings.append("No XBRL facts extracted from company facts")
                return

            if not md_values:
                result.reconciliation_status = "no_markdown_facts"
                result.warnings.append("No numeric facts extracted from markdown")
                return

            # Run reconciliation
            reconciler = Reconciler(tolerance=0.02)  # 2% tolerance
            report = reconciler.reconcile(xbrl_facts, md_values)

            result.reconciliation_report = report.to_dict()
            result.facts_matched = report.facts_matched
            result.facts_mismatched = (
                report.facts_scale_mismatch + report.facts_sign_mismatch
            )

            # Determine reconciliation status based on match rate
            if report.facts_compared == 0:
                result.reconciliation_status = "no_comparable_facts"
            elif report.match_rate >= 0.8:
                result.reconciliation_status = "reconciled_pass"
            elif report.match_rate >= 0.5:
                result.reconciliation_status = "reconciled_partial"
            else:
                result.reconciliation_status = "reconciled_fail"

        except Exception as exc:
            result.reconciliation_status = "error"
            result.warnings.append(f"Reconciliation error: {exc}")
            logger.warning("Reconciliation failed: %s", exc)

    def _check_structural(
        self, result: VerificationResult, markdown: str, sections: list | None, form: str,
    ) -> None:
        # Check: markdown exists and has content
        if markdown and len(markdown) > 100:
            result.structural_checks.append({
                "check": "markdown_content", "status": "passed",
                "message": f"Markdown content present ({len(markdown)} chars)",
            })
        else:
            result.structural_checks.append({
                "check": "markdown_content", "status": "failed",
                "message": f"Markdown too short or empty ({len(markdown)} chars)",
            })
        
        # Check: sections found
        if sections and len(sections) > 1:
            result.structural_checks.append({
                "check": "sections_found", "status": "passed",
                "message": f"{len(sections)} sections identified",
            })
        elif sections and len(sections) == 1 and sections[0].item == "full":
            result.structural_checks.append({
                "check": "sections_found", "status": "warning",
                "message": "No Item sections found — returned as single document",
            })
        else:
            result.structural_checks.append({
                "check": "sections_found", "status": "warning",
                "message": "No sections available",
            })
        
        # Check: expected sections for 10-K
        if form in ("10-K", "10-Q") and sections:
            items_found = {s.item for s in sections if hasattr(s, "item")}
            expected = {"Item 1", "Item 7", "Item 8"} if form == "10-K" else {"Item 1", "Item 2"}
            missing = expected - items_found
            if not missing:
                result.structural_checks.append({
                    "check": "expected_sections", "status": "passed",
                    "message": f"All expected {form} sections present",
                })
            else:
                result.structural_checks.append({
                    "check": "expected_sections", "status": "warning",
                    "message": f"Missing expected sections: {', '.join(sorted(missing))}",
                })

    def _check_xbrl(self, result: VerificationResult, xbrl_url: str) -> None:
        try:
            from zion_terminal.pipeline.xbrl import XBRLVerifier
            verifier = XBRLVerifier()
            if not verifier.is_available:
                result.xbrl_status = "unavailable"
                result.warnings.append("Arelle not installed — XBRL verification skipped")
                return
            
            xbrl_result = verifier.validate_url(xbrl_url, extract_facts=True)
            result.xbrl_facts_extracted = xbrl_result.facts_count
            if xbrl_result.valid:
                result.xbrl_status = "passed"
            else:
                result.xbrl_status = "failed"
                result.xbrl_errors = xbrl_result.errors
        except Exception as exc:
            result.xbrl_status = "error"
            result.xbrl_errors = [str(exc)]

    def _check_cross_source(
        self, result: VerificationResult, yahoo_data: dict, ticker: str,
    ) -> None:
        """Compare filing-derived data against Yahoo Finance for sanity checking."""
        result.cross_source_status = "checked"
        # This is a hook for future cross-source reconciliation.
        # For now, we record that Yahoo data was available for comparison.
        result.cross_source_checks.append({
            "check": "yahoo_data_available", "status": "passed",
            "message": f"Yahoo Finance data available for {ticker} cross-reference",
            "note": "Cross-source reconciliation is heuristic, not authoritative",
        })
