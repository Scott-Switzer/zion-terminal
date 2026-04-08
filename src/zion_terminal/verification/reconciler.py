"""Reconcile XBRL facts against markdown-extracted values."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any

from zion_terminal.verification.fact_mapping import CanonicalFact
from zion_terminal.verification.markdown_extractor import ExtractedValue

logger = logging.getLogger(__name__)

# Common GAAP concept → markdown label mappings
_LABEL_MAP: dict[str, list[str]] = {
    "Revenues": ["Revenue", "Total Revenue", "Net Revenue", "Sales"],
    "CostOfGoodsAndServicesSold": ["Cost of Revenue", "COGS", "Cost of Goods Sold", "Cost Of Revenue"],
    "GrossProfit": ["Gross Profit", "Gross Income"],
    "NetIncomeLoss": ["Net Income", "Net Income (Loss)", "Net Income Loss"],
    "Assets": ["Total Assets"],
    "Liabilities": ["Total Liabilities"],
    "StockholdersEquity": ["Total Equity", "Stockholders Equity", "Total Stockholders' Equity"],
    "OperatingIncomeLoss": ["Operating Income", "Operating Income (Loss)"],
    "EarningsPerShareBasic": ["Basic EPS", "Earnings Per Share Basic"],
}


@dataclass
class ReconciliationMatch:
    """A single fact-to-markdown comparison."""
    concept: str
    xbrl_value: float | str | None
    markdown_value: float | None
    markdown_label: str
    status: str = "unknown"  # matched | scale_mismatch | sign_mismatch | label_mismatch | missing
    delta: float | None = None
    delta_pct: float | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "xbrl_value": self.xbrl_value,
            "markdown_value": self.markdown_value,
            "markdown_label": self.markdown_label,
            "status": self.status,
            "delta": self.delta,
            "delta_pct": self.delta_pct,
            "notes": self.notes,
        }


@dataclass
class ReconciliationReport:
    """Full reconciliation output."""
    facts_compared: int = 0
    facts_matched: int = 0
    facts_scale_mismatch: int = 0
    facts_sign_mismatch: int = 0
    facts_missing: int = 0
    matches: list[ReconciliationMatch] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        return self.facts_matched / self.facts_compared if self.facts_compared else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts_compared": self.facts_compared,
            "facts_matched": self.facts_matched,
            "facts_scale_mismatch": self.facts_scale_mismatch,
            "facts_sign_mismatch": self.facts_sign_mismatch,
            "facts_missing": self.facts_missing,
            "match_rate": round(self.match_rate, 4),
            "matches": [m.to_dict() for m in self.matches],
        }


class Reconciler:
    """Compare XBRL canonical facts against markdown-extracted values."""

    def __init__(self, tolerance: float = 0.01) -> None:
        self._tolerance = tolerance  # 1% default tolerance

    def reconcile(
        self,
        xbrl_facts: list[CanonicalFact],
        markdown_values: list[ExtractedValue],
    ) -> ReconciliationReport:
        report = ReconciliationReport()
        
        # Build a label lookup from markdown values
        md_by_label: dict[str, ExtractedValue] = {}
        for mv in markdown_values:
            md_by_label[mv.label.lower().strip()] = mv
        
        for fact in xbrl_facts:
            if not isinstance(fact.value, (int, float)):
                continue  # Only reconcile numeric facts
            
            report.facts_compared += 1
            match = self._find_match(fact, md_by_label)
            report.matches.append(match)
            
            if match.status == "matched":
                report.facts_matched += 1
            elif match.status == "scale_mismatch":
                report.facts_scale_mismatch += 1
            elif match.status == "sign_mismatch":
                report.facts_sign_mismatch += 1
            elif match.status == "missing":
                report.facts_missing += 1
        
        return report

    def _find_match(
        self, fact: CanonicalFact, md_by_label: dict[str, ExtractedValue],
    ) -> ReconciliationMatch:
        """Try to match a single XBRL fact to a markdown value."""
        # Try direct label match and known aliases
        candidate_labels = [fact.local_name.lower()]
        for gaap_name, aliases in _LABEL_MAP.items():
            if fact.local_name == gaap_name:
                candidate_labels.extend(a.lower() for a in aliases)
        
        for label in candidate_labels:
            if label in md_by_label:
                mv = md_by_label[label]
                return self._compare(fact, mv)
        
        return ReconciliationMatch(
            concept=fact.concept,
            xbrl_value=fact.value,
            markdown_value=None,
            markdown_label="",
            status="missing",
            notes=f"No markdown match for {fact.local_name}",
        )

    def _compare(self, fact: CanonicalFact, mv: ExtractedValue) -> ReconciliationMatch:
        """Compare a matched pair."""
        xv = float(fact.value) if isinstance(fact.value, (int, float)) else 0
        mdv = mv.value if mv.value is not None else 0
        
        delta = abs(xv - mdv)
        denom = max(abs(xv), abs(mdv), 1)
        delta_pct = delta / denom
        
        # Check for scale mismatch (off by 1000x or 1000000x)
        if delta_pct > self._tolerance and denom > 0:
            for scale in [1000, 1_000_000, 1_000_000_000]:
                if abs(xv - mdv * scale) / max(abs(xv), 1) < self._tolerance:
                    return ReconciliationMatch(
                        concept=fact.concept, xbrl_value=xv,
                        markdown_value=mdv, markdown_label=mv.label,
                        status="scale_mismatch", delta=delta, delta_pct=delta_pct,
                        notes=f"Markdown appears to be in units of {scale}x",
                    )
                if abs(xv * scale - mdv) / max(abs(mdv), 1) < self._tolerance:
                    return ReconciliationMatch(
                        concept=fact.concept, xbrl_value=xv,
                        markdown_value=mdv, markdown_label=mv.label,
                        status="scale_mismatch", delta=delta, delta_pct=delta_pct,
                        notes=f"XBRL appears to be in units of {scale}x",
                    )
        
        # Check sign mismatch
        if xv != 0 and mdv != 0 and (xv > 0) != (mdv > 0):
            if abs(abs(xv) - abs(mdv)) / denom < self._tolerance:
                return ReconciliationMatch(
                    concept=fact.concept, xbrl_value=xv,
                    markdown_value=mdv, markdown_label=mv.label,
                    status="sign_mismatch", delta=delta, delta_pct=delta_pct,
                )
        
        if delta_pct <= self._tolerance:
            return ReconciliationMatch(
                concept=fact.concept, xbrl_value=xv,
                markdown_value=mdv, markdown_label=mv.label,
                status="matched", delta=delta, delta_pct=delta_pct,
            )
        
        return ReconciliationMatch(
            concept=fact.concept, xbrl_value=xv,
            markdown_value=mdv, markdown_label=mv.label,
            status="label_mismatch", delta=delta, delta_pct=delta_pct,
            notes=f"Values differ by {delta_pct:.1%}",
        )
