"""Section segmenter for SEC filings.

Splits a 10-K or 10-Q markdown document into its standard sections
(Items) so they can be accessed, searched, or analyzed individually.

Standard 10-K sections:
  Item 1   — Business
  Item 1A  — Risk Factors
  Item 1B  — Unresolved Staff Comments
  Item 2   — Properties
  Item 3   — Legal Proceedings
  Item 4   — Mine Safety Disclosures
  Item 5   — Market for Registrant's Common Equity
  Item 6   — [Reserved]
  Item 7   — Management's Discussion and Analysis (MD&A)
  Item 7A  — Quantitative and Qualitative Disclosures About Market Risk
  Item 8   — Financial Statements and Supplementary Data
  Item 9   — Changes in and Disagreements With Accountants
  Item 9A  — Controls and Procedures
  Item 10  — Directors, Executive Officers and Corporate Governance
  Item 11  — Executive Compensation
  Item 12  — Security Ownership
  Item 13  — Certain Relationships
  Item 14  — Principal Accountant Fees
  Item 15  — Exhibits and Financial Statement Schedules
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FilingSection:
    """A single section of an SEC filing."""

    item: str                   # e.g. "Item 1A"
    title: str = ""             # e.g. "Risk Factors"
    content: str = ""           # markdown text
    start_line: int = 0
    end_line: int = 0

    @property
    def char_count(self) -> int:
        return len(self.content)

    @property
    def is_empty(self) -> bool:
        return len(self.content.strip()) == 0


# Pattern to match "Item 1", "Item 1A", "Item 7A", "ITEM 15" etc.
# Handles varying formats: "Item 1.", "ITEM 1 -", "Item 1:", etc.
_ITEM_PATTERN = re.compile(
    r"^#{1,4}\s*(?:ITEM|Item)\s+(\d{1,2}[A-Ba-b]?)\b[\.\:\-—–]?\s*(.*?)$",
    re.MULTILINE,
)

# Known section titles for fuzzy matching
_KNOWN_TITLES: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Reserved",
    "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements With Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
}


class FilingSegmenter:
    """Splits filing markdown into sections by Item number.

    Usage::

        segmenter = FilingSegmenter()
        sections = segmenter.segment(markdown_text)
        for section in sections:
            print(f"{section.item}: {section.title} ({section.char_count} chars)")

        # Get a specific section
        risk_factors = segmenter.get_section(sections, "1A")
    """

    def segment(self, markdown: str) -> list[FilingSection]:
        """Split markdown into sections based on Item headers."""
        lines = markdown.split("\n")
        matches: list[tuple[int, str, str]] = []

        for i, line in enumerate(lines):
            m = _ITEM_PATTERN.match(line)
            if m:
                item_num = m.group(1).upper()
                title = m.group(2).strip()
                if not title:
                    title = _KNOWN_TITLES.get(item_num, "")
                matches.append((i, item_num, title))

        if not matches:
            # No items found — return the whole thing as one section
            return [FilingSection(
                item="full",
                title="Full Document",
                content=markdown,
                start_line=0,
                end_line=len(lines),
            )]

        sections: list[FilingSection] = []
        for idx, (line_num, item_num, title) in enumerate(matches):
            end_line = matches[idx + 1][0] if idx + 1 < len(matches) else len(lines)
            content = "\n".join(lines[line_num:end_line]).strip()
            sections.append(FilingSection(
                item=f"Item {item_num}",
                title=title,
                content=content,
                start_line=line_num,
                end_line=end_line,
            ))

        return sections

    @staticmethod
    def get_section(sections: list[FilingSection], item: str) -> FilingSection | None:
        """Find a section by item number (e.g. '1A', 'Item 7')."""
        normalized = item.upper().replace("ITEM ", "").strip()
        for s in sections:
            s_num = s.item.upper().replace("ITEM ", "").strip()
            if s_num == normalized:
                return s
        return None

    @staticmethod
    def summary(sections: list[FilingSection]) -> dict[str, Any]:
        """Return a summary of all sections."""
        return {
            "section_count": len(sections),
            "sections": [
                {
                    "item": s.item,
                    "title": s.title,
                    "char_count": s.char_count,
                    "is_empty": s.is_empty,
                }
                for s in sections
            ],
            "total_chars": sum(s.char_count for s in sections),
        }
