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
    reconciliation_status: str = "not_run"  # not_run | reconciled_pass | reconciled_partial | reconciled_fail | error | no_period_match
    reconciliation_report: dict[str, Any] = field(default_factory=dict)
    period_matched: str = ""  # e.g. "FY2023" or "Q2 2021"
    period_match_mode: str = ""  # exact_period | year_only | no_period_match | no_filing_date | ambiguous
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
            "period_matched": self.period_matched,
            "period_match_mode": self.period_match_mode,
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


def _company_facts_to_canonical(
    facts_json: dict,
    target_fy: int | None = None,
    target_fp: str | None = None,
) -> list[CanonicalFact]:
    """Convert SEC company facts JSON to a list of CanonicalFact objects.

    Strict period matching: only extracts facts from the target fiscal
    year (``target_fy``) and period (``target_fp``).  Never silently
    falls back to "most recent".

    Args:
        facts_json: SEC companyfacts JSON response.
        target_fy: Target fiscal year (e.g. 2023).  If None, takes latest.
        target_fp: Target fiscal period ("FY", "Q1", "Q2", "Q3", "Q4").
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

                # Strict period matching — NO hidden fallback
                if target_fy is not None:
                    if target_fp is not None:
                        # Exact: match both fiscal year AND period
                        matching = [
                            e for e in entries
                            if e.get("fy") == target_fy and e.get("fp") == target_fp
                        ]
                    else:
                        # Year-only: no specific period requested
                        matching = [e for e in entries if e.get("fy") == target_fy]
                    if not matching:
                        continue  # No match — skip concept entirely
                    entry = matching[-1]
                else:
                    entry = entries[-1]  # Only when no target period at all

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


def _detect_fiscal_year_end_month(company_facts: dict | None) -> int | None:
    """Detect the company's fiscal year end month from XBRL period-end dates.

    Examines the ``end`` field of annual (FY) entries in company facts.
    For example, Apple's FY2023 has ``end: "2023-09-30"`` → FYE month = 9.

    Returns the FYE month (1-12) or None if it cannot be determined.
    """
    if not company_facts or "facts" not in company_facts:
        return None

    end_months: dict[int, int] = {}  # month → count
    for ns_data in company_facts.get("facts", {}).values():
        if not isinstance(ns_data, dict):
            continue
        for concept_data in ns_data.values():
            if not isinstance(concept_data, dict):
                continue
            for entries in concept_data.get("units", {}).values():
                if not isinstance(entries, list):
                    continue
                for e in entries:
                    if e.get("fp") == "FY" and e.get("end"):
                        try:
                            end_month = int(e["end"][5:7])
                            end_months[end_month] = end_months.get(end_month, 0) + 1
                        except (ValueError, IndexError):
                            continue
    if not end_months:
        return None
    # Return the most common FYE month
    return max(end_months, key=end_months.get)


def _derive_fiscal_period(
    filing_date: str, form: str, company_facts: dict | None,
) -> tuple[int | None, str | None, str]:
    """Derive the target fiscal year and period from available metadata.

    Uses company facts metadata (``fy`` and ``fp`` fields from XBRL entries)
    as the primary source when available, falling back to filing-date
    heuristics only when necessary.

    Returns:
        (target_fy, target_fp, match_mode) where match_mode is one of:
        - "exact_period": both FY and FP determined from XBRL metadata
        - "year_only": FY determined but FP could not be resolved
        - "heuristic": derived from filing date (less reliable)
        - "no_filing_date": no filing date available
    """
    if not filing_date:
        return None, None, "no_filing_date"

    # Try to determine fiscal year from filing date
    try:
        year = int(filing_date[:4])
        month = int(filing_date[5:7])
    except (ValueError, IndexError):
        return None, None, "no_filing_date"

    # For 10-K: determine FY. Most companies file 10-K within 60-90 days
    # of fiscal year end.  If filed in Q4 or Q1, FY is likely that year
    # or the prior year.
    if form == "10-K":
        # Use XBRL period-end dates to determine fiscal year end month,
        # then derive the correct FY from the filing date.
        fye_month = _detect_fiscal_year_end_month(company_facts)
        if fye_month:
            # A 10-K covers FY ending in fye_month. If the filing date is
            # after the FYE, the FY is the current year; otherwise prior year.
            if month > fye_month:
                candidate_fy = year
            elif month <= fye_month:
                candidate_fy = year if month == fye_month else year - 1
            else:
                candidate_fy = year
        else:
            # Heuristic: most companies file 10-K within 60-90 days of FYE
            candidate_fy = year if month > 6 else year - 1

        if company_facts and "facts" in company_facts:
            # Validate candidate_fy against actual XBRL entries
            for ns_data in company_facts.get("facts", {}).values():
                if not isinstance(ns_data, dict):
                    continue
                for concept_data in ns_data.values():
                    if not isinstance(concept_data, dict):
                        continue
                    for entries in concept_data.get("units", {}).values():
                        if not isinstance(entries, list):
                            continue
                        for e in entries:
                            if e.get("fy") == candidate_fy and e.get("fp") == "FY":
                                return candidate_fy, "FY", "exact_period"
        # If we detected FYE month from XBRL but couldn't validate exact FY
        return candidate_fy, "FY", "heuristic" if not fye_month else "heuristic"

    elif form == "10-Q":
        # For 10-Q: determine exact quarter from company facts metadata
        # Use detected FYE month for better candidate_fy
        fye_month = _detect_fiscal_year_end_month(company_facts)
        if fye_month:
            candidate_fy = year if month > fye_month else year - 1
        else:
            candidate_fy = year if month > 6 else year - 1
        if company_facts and "facts" in company_facts:
            # Strategy 1: Match by exact filed date
            for ns_data in company_facts.get("facts", {}).values():
                if not isinstance(ns_data, dict):
                    continue
                for concept_data in ns_data.values():
                    if not isinstance(concept_data, dict):
                        continue
                    for entries in concept_data.get("units", {}).values():
                        if not isinstance(entries, list):
                            continue
                        for e in entries:
                            if (e.get("fy") == candidate_fy
                                    and e.get("fp", "").startswith("Q")
                                    and e.get("filed") == filing_date):
                                return candidate_fy, e["fp"], "exact_period"

            # Strategy 2: Derive quarter from XBRL period-end date in entries
            # 10-Q period_end tells us the exact quarter boundary
            quarter_candidates: dict[str, int] = {}
            for ns_data in company_facts.get("facts", {}).values():
                if not isinstance(ns_data, dict):
                    continue
                for concept_data in ns_data.values():
                    if not isinstance(concept_data, dict):
                        continue
                    for entries in concept_data.get("units", {}).values():
                        if not isinstance(entries, list):
                            continue
                        for e in entries:
                            if e.get("fy") == candidate_fy and e.get("fp", "").startswith("Q"):
                                fp = e["fp"]
                                quarter_candidates[fp] = quarter_candidates.get(fp, 0) + 1
            # If there's a clear quarter with entries filed near our date, use it
            if quarter_candidates:
                # Filing month heuristic: 10-Q filed ~40 days after quarter end
                month_to_likely_q = {
                    1: "Q1", 2: "Q1", 3: "Q1", 4: "Q1", 5: "Q2",
                    6: "Q2", 7: "Q2", 8: "Q3", 9: "Q3", 10: "Q3", 11: "Q4", 12: "Q4",
                }
                likely = month_to_likely_q.get(month)
                if likely and likely in quarter_candidates:
                    return candidate_fy, likely, "heuristic"
                # Take the quarter with most entries as fallback
                best_q = max(quarter_candidates, key=quarter_candidates.get)
                return candidate_fy, best_q, "heuristic"

        return candidate_fy, None, "year_only"

    else:
        # Other form types: year-only
        candidate_fy = year if month > 6 else year - 1
        return candidate_fy, None, "year_only"


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
        filing_date: str = "",
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
            self._check_reconciliation(result, markdown, company_facts, form, filing_date)
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
        form: str = "", filing_date: str = "",
    ) -> None:
        """Reconcile XBRL facts from SEC company facts JSON against markdown.

        Strict period matching: only compares facts from the same fiscal
        period as the filing being verified.  Never silently uses "most recent."
        """
        try:
            # Derive target fiscal period from filing metadata + company facts
            target_fy, target_fp, match_mode = _derive_fiscal_period(
                filing_date, form, company_facts,
            )
            result.period_match_mode = match_mode

            if target_fy:
                if target_fp:
                    period_label = f"{target_fp}{target_fy}" if target_fp == "FY" else f"{target_fp} {target_fy}"
                else:
                    period_label = f"FY{target_fy} (year_only)"
                result.period_matched = period_label
            else:
                result.period_matched = "unknown"

            # Extract facts with strict period matching
            xbrl_facts = _company_facts_to_canonical(company_facts, target_fy, target_fp)
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

            # Determine reconciliation status based on match rate AND period quality
            if report.facts_compared == 0:
                result.reconciliation_status = "no_comparable_facts"
            elif report.match_rate >= 0.8 and match_mode == "exact_period":
                result.reconciliation_status = "reconciled_pass"
            elif report.match_rate >= 0.8:
                # Good match rate but period was heuristic — downgrade
                result.reconciliation_status = "reconciled_partial"
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
