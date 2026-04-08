"""Filing HTML → structured markdown converter.

Uses BeautifulSoup for DOM-based conversion. Falls back gracefully
if bs4/lxml are unavailable (returns raw text stripping).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Pattern to detect SEC Item headers in text content.
# Matches "Item 1", "ITEM 1A.", "Item 7A —", etc.
_ITEM_HEADER_RE = re.compile(
    r"^\s*(?:ITEM|Item)\s+\d{1,2}[A-Ba-b]?\b[\.\:\-\—\–]?\s*.{0,120}$"
)


class FilingConverter:
    """Converts SEC filing HTML to clean markdown.

    Usage::

        converter = FilingConverter()
        result = converter.convert(html_content, metadata={"ticker": "AAPL", "form": "10-K"})
        # result["markdown"] — the full markdown text
        # result["metadata"] — passthrough + converter metadata
    """

    def __init__(self, max_length: int = 200_000) -> None:
        self._max_length = max_length

    def convert(self, html: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Convert HTML to markdown.

        Returns a dict with ``markdown``, ``metadata``, and ``char_count``.
        """
        used_dom = False
        try:
            from bs4 import BeautifulSoup, NavigableString, Tag
            markdown = self._dom_convert(html, BeautifulSoup, NavigableString, Tag)
            used_dom = True
        except ImportError:
            logger.warning("bs4/lxml not available — falling back to regex stripping")
            markdown = self._fallback_convert(html)

        if len(markdown) > self._max_length:
            markdown = markdown[:self._max_length] + "\n\n[... truncated ...]"

        return {
            "markdown": markdown,
            "char_count": len(markdown),
            "metadata": {
                **(metadata or {}),
                "converter": "dom" if used_dom else "regex",
                "truncated": len(markdown) >= self._max_length,
            },
        }

    def _dom_convert(self, html: str, BeautifulSoup, NavigableString, Tag) -> str:
        soup = BeautifulSoup(html, "lxml")

        # Remove non-content elements
        for tag in soup.find_all(["script", "style", "meta", "link", "noscript"]):
            tag.decompose()

        parts: list[str] = []
        self._walk(soup, parts, NavigableString, Tag)
        text = "".join(parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _walk(self, element, parts: list[str], NavigableString, Tag) -> None:
        if isinstance(element, NavigableString):
            text = str(element).strip()
            if text:
                parts.append(text + " ")
            return

        if not isinstance(element, Tag):
            return

        name = element.name.lower() if element.name else ""

        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(name[1])
            text = element.get_text(strip=True)
            if text:
                parts.append(f"\n\n{'#' * level} {text}\n\n")
            return

        if name == "p":
            text = element.get_text(strip=True)
            # Promote SEC Item headers inside <p> tags to markdown headings
            if text and _ITEM_HEADER_RE.match(text):
                parts.append(f"\n\n## {text}\n\n")
                return
            # Recurse into children to preserve inline formatting (bold, italic)
            parts.append("\n\n")
            for child in element.children:
                self._walk(child, parts, NavigableString, Tag)
            parts.append("\n")
            return

        if name == "br":
            parts.append("\n")
            return

        if name in ("b", "strong"):
            text = element.get_text(strip=True)
            if not text:
                return
            # Promote SEC Item headers inside <b>/<strong> to markdown headings
            if _ITEM_HEADER_RE.match(text):
                parts.append(f"\n\n## {text}\n\n")
                return
            parts.append(f"**{text}**")
            return

        if name in ("i", "em"):
            text = element.get_text(strip=True)
            if text:
                parts.append(f"*{text}*")
            return

        if name == "li":
            text = element.get_text(strip=True)
            if text:
                parts.append(f"\n- {text}")
            return

        if name == "table":
            self._convert_table(element, parts, Tag)
            return

        # For <div> elements, check if they are SEC Item headers
        if name == "div":
            text = element.get_text(strip=True)
            if text and _ITEM_HEADER_RE.match(text) and len(text) < 150:
                parts.append(f"\n\n## {text}\n\n")
                return

        for child in element.children:
            self._walk(child, parts, NavigableString, Tag)

    @staticmethod
    def _convert_table(table, parts: list[str], Tag) -> None:
        rows = table.find_all("tr")
        if not rows:
            return

        md_rows: list[list[str]] = []
        for row in rows:
            cells = row.find_all(["td", "th"])
            md_row = [c.get_text(strip=True).replace("|", "/") for c in cells]
            if any(md_row):
                md_rows.append(md_row)

        if not md_rows:
            return

        max_cols = max(len(r) for r in md_rows)
        for r in md_rows:
            while len(r) < max_cols:
                r.append("")

        parts.append("\n\n")
        parts.append("| " + " | ".join(md_rows[0]) + " |\n")
        parts.append("| " + " | ".join(["---"] * max_cols) + " |\n")
        for row in md_rows[1:]:
            parts.append("| " + " | ".join(row) + " |\n")

    @staticmethod
    def _fallback_convert(html: str) -> str:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
