"""Extract numeric values from markdown tables for reconciliation.

Includes scale inference: detects "in millions", "in thousands", etc.
from markdown text context and applies the multiplier to extracted values.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any

# Scale detection patterns — ordered longest first
_SCALE_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\$\s*in\s+billions?", re.IGNORECASE), 1_000_000_000),
    (re.compile(r"\$\s*in\s+millions?", re.IGNORECASE), 1_000_000),
    (re.compile(r"\$\s*in\s+thousands?", re.IGNORECASE), 1_000),
    (re.compile(r"\(in\s+billions?\)", re.IGNORECASE), 1_000_000_000),
    (re.compile(r"\(in\s+millions?\)", re.IGNORECASE), 1_000_000),
    (re.compile(r"\(in\s+thousands?\)", re.IGNORECASE), 1_000),
    (re.compile(r"\bin\s+billions?", re.IGNORECASE), 1_000_000_000),
    (re.compile(r"\bin\s+millions?", re.IGNORECASE), 1_000_000),
    (re.compile(r"\bin\s+thousands?", re.IGNORECASE), 1_000),
]


def detect_scale_context(text: str) -> int:
    """Detect scale multiplier from text context.

    Scans for phrases like "in millions", "$ in thousands", etc.
    Returns the multiplier (1_000, 1_000_000, 1_000_000_000, or 1).
    """
    for pattern, scale in _SCALE_PATTERNS:
        if pattern.search(text):
            return scale
    return 1


@dataclass
class ExtractedValue:
    """A numeric value extracted from markdown."""
    label: str
    value: float | None
    raw_text: str = ""
    table_index: int = 0
    row_index: int = 0
    scale_applied: int = 1
    scaled_value: float | None = None


def extract_tables(markdown: str) -> list[list[list[str]]]:
    """Parse markdown into a list of tables, each a list of rows, each a list of cells."""
    tables: list[list[list[str]]] = []
    current_table: list[list[str]] = []
    in_table = False

    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            # Skip separator rows
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                continue
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            current_table.append(cells)
            in_table = True
        else:
            if in_table and current_table:
                tables.append(current_table)
                current_table = []
            in_table = False

    if current_table:
        tables.append(current_table)

    return tables


def parse_numeric(text: str) -> float | None:
    """Parse a numeric value from financial text.
    
    Handles: $1,234.56, (1,234), -1234, 1.2M, 1.2B, etc.
    """
    if not text or not text.strip():
        return None
    
    cleaned = text.strip()
    
    # Remove currency symbols
    cleaned = re.sub(r"[$€£¥]", "", cleaned)
    
    # Handle parenthetical negatives: (1,234) → -1234
    neg = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        neg = True
        cleaned = cleaned[1:-1]
    
    # Handle leading minus
    if cleaned.startswith("-"):
        neg = True
        cleaned = cleaned[1:]
    
    # Remove commas and spaces
    cleaned = cleaned.replace(",", "").replace(" ", "")
    
    # Handle scale suffixes
    scale = 1
    if cleaned.upper().endswith("B"):
        scale = 1_000_000_000
        cleaned = cleaned[:-1]
    elif cleaned.upper().endswith("M"):
        scale = 1_000_000
        cleaned = cleaned[:-1]
    elif cleaned.upper().endswith("K"):
        scale = 1_000
        cleaned = cleaned[:-1]
    elif cleaned.upper().endswith("T"):  # trillion
        scale = 1_000_000_000_000
        cleaned = cleaned[:-1]
    
    # Handle percentage
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
        try:
            val = float(cleaned) / 100
            return -val if neg else val
        except ValueError:
            return None
    
    try:
        val = float(cleaned) * scale
        return -val if neg else val
    except ValueError:
        return None


def extract_values(markdown: str) -> list[ExtractedValue]:
    """Extract labeled numeric values from markdown tables.

    Detects scale context ("in millions", etc.) from nearby text and
    applies the multiplier to produce ``scaled_value``.
    """
    scale = detect_scale_context(markdown)
    results: list[ExtractedValue] = []
    tables = extract_tables(markdown)
    
    for t_idx, table in enumerate(tables):
        if len(table) < 2:
            continue
        # First row is header, rest are data
        for r_idx, row in enumerate(table[1:], start=1):
            if len(row) >= 2:
                label = row[0].strip()
                for c_idx in range(1, len(row)):
                    val = parse_numeric(row[c_idx])
                    if val is not None:
                        results.append(ExtractedValue(
                            label=label,
                            value=val,
                            raw_text=row[c_idx].strip(),
                            table_index=t_idx,
                            row_index=r_idx,
                            scale_applied=scale,
                            scaled_value=val * scale if scale != 1 else val,
                        ))
                        break  # Take first numeric column
    
    return results
