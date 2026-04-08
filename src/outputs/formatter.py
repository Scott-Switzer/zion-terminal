"""Output formatters: Markdown, CSV, JSON.

All outputs are optimised for LLM consumption and data pipeline integration.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from enum import Enum
from typing import Any

from src.models.responses import (
    OrchestratorResponse,
    RetrievalResult,
    SynthesisResult,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    CSV = "csv"
    JSON = "json"


class OutputFormatter:
    """Convert OrchestratorResponse into the desired output format."""

    def format(
        self,
        response: OrchestratorResponse,
        fmt: OutputFormat = OutputFormat.MARKDOWN,
    ) -> str:
        """Render the response in the chosen format."""
        if fmt == OutputFormat.JSON:
            return self._to_json(response)
        elif fmt == OutputFormat.CSV:
            return self._to_csv(response)
        else:
            return self._to_markdown(response)

    # ── JSON ─────────────────────────────────────────────────────

    def _to_json(self, response: OrchestratorResponse) -> str:
        """Full JSON dump of the response."""
        payload = {
            "success": response.success,
            "query": response.query,
            "intent": response.intent,
            "data": response.all_data,
            "errors": response.errors,
            "metadata": response.metadata,
        }
        return json.dumps(payload, indent=2, default=str)

    # ── CSV ──────────────────────────────────────────────────────

    def _to_csv(self, response: OrchestratorResponse) -> str:
        """Flatten data into CSV.  Best-effort for heterogeneous data."""
        data = response.all_data
        if not data:
            return "# No data returned\n"

        buf = io.StringIO()
        # Collect all keys across all records
        all_keys: list[str] = []
        for item in data:
            flat = self._flatten(item)
            for k in flat:
                if k not in all_keys:
                    all_keys.append(k)

        writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        for item in data:
            writer.writerow(self._flatten(item))

        return buf.getvalue()

    # ── Markdown ─────────────────────────────────────────────────

    def _to_markdown(self, response: OrchestratorResponse) -> str:
        """Rich Markdown output suitable for terminals and LLMs."""
        lines: list[str] = []
        lines.append(f"# Zion Terminal – Query Results\n")
        lines.append(f"**Query:** {response.query}")
        lines.append(f"**Intent:** {response.intent}")
        lines.append(f"**Success:** {'Yes' if response.success else 'No'}\n")

        if response.errors:
            lines.append("## Errors\n")
            for err in response.errors:
                lines.append(f"- {err}")
            lines.append("")

        for result in response.results:
            if isinstance(result, RetrievalResult):
                lines.extend(self._render_retrieval_md(result))
            elif isinstance(result, ValidationResult):
                lines.extend(self._render_validation_md(result))
            elif isinstance(result, SynthesisResult):
                lines.extend(self._render_synthesis_md(result))

        return "\n".join(lines)

    def _render_retrieval_md(self, result: RetrievalResult) -> list[str]:
        lines = [
            "## Retrieved Data\n",
            f"Sources: {', '.join(result.sources_used)}",
            f"Cached: {'Yes' if result.cached else 'No'}\n",
        ]

        for item in result.data:
            item_type = item.get("type", item.get("statement_type", "data"))
            lines.append(f"### {item_type}\n")

            # Stock quote
            if "ticker" in item and "price" in item:
                lines.append(f"**{item.get('company_name', item['ticker'])}** ({item['ticker']})\n")
                for key in ["price", "open", "high", "low", "close", "volume",
                           "market_cap", "pe_ratio", "dividend_yield",
                           "fifty_two_week_high", "fifty_two_week_low"]:
                    val = item.get(key)
                    if val is not None:
                        label = key.replace("_", " ").title()
                        if isinstance(val, float) and val > 1000:
                            lines.append(f"| {label} | {val:,.2f} |")
                        else:
                            lines.append(f"| {label} | {val} |")
                lines.append("")

            # Time series
            elif "data_points" in item and isinstance(item["data_points"], list):
                pts = item["data_points"]
                lines.append(f"**{item.get('name', 'Time Series')}** ({len(pts)} points)\n")
                if pts:
                    # Show first and last few
                    show = pts[:3] + [{"date": "...", "value": "..."}] + pts[-3:] if len(pts) > 6 else pts
                    headers = list(show[0].keys())
                    lines.append("| " + " | ".join(headers) + " |")
                    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                    for pt in show:
                        vals = [str(pt.get(h, "")) for h in headers]
                        lines.append("| " + " | ".join(vals) + " |")
                lines.append("")

            # Financial statements
            elif "line_items" in item:
                lines.append(f"**{item.get('company_name', '')}** – {item.get('period', '')}\n")
                lines.append("| Line Item | Value |")
                lines.append("| --- | ---: |")
                for k, v in item.get("line_items", {}).items():
                    if v is not None:
                        lines.append(f"| {k} | {v:,.0f} |")
                lines.append("")

            # FRED / macro
            elif "series_id" in item:
                lines.append(f"**{item.get('title', item['series_id'])}**\n")
                lines.append(f"- Latest value: {item.get('value')}")
                lines.append(f"- Observation date: {item.get('observation_date')}")
                lines.append(f"- Unit: {item.get('unit')}")
                lines.append(f"- Frequency: {item.get('frequency')}")
                lines.append("")

            # SEC filings
            elif "filing_type" in item:
                lines.append(
                    f"- **{item.get('filing_type')}** filed {item.get('filing_date', 'N/A')} "
                    f"(accession: {item.get('accession_number', 'N/A')})"
                )

            # Generic fallback
            else:
                for k, v in item.items():
                    if k not in ("source", "type") and v is not None:
                        lines.append(f"- **{k}:** {v}")
                lines.append("")

        return lines

    def _render_validation_md(self, result: ValidationResult) -> list[str]:
        lines = [
            "## Validation\n",
            f"Status: **{result.status.value}**",
            f"Checks: {result.checks_passed}/{result.checks_run} passed\n",
        ]
        if result.details:
            for d in result.details:
                lines.append(f"- [{d.get('status', '?').upper()}] {d.get('check')}: {d.get('message')}")
        if result.warnings:
            lines.append("\n**Warnings:**")
            for w in result.warnings:
                lines.append(f"- {w}")
        lines.append("")
        return lines

    def _render_synthesis_md(self, result: SynthesisResult) -> list[str]:
        lines = [
            f"## Synthetic Entity: {result.entity_name} ({result.entity_ticker})\n",
        ]
        for doc in result.documents:
            doc_type = doc.get("type", "document")
            data = doc.get("data", {})
            lines.append(f"### {doc_type.replace('_', ' ').title()}\n")

            if doc_type == "press_release":
                lines.append(data.get("content", ""))
            elif isinstance(data, dict):
                lines.append("| Field | Value |")
                lines.append("| --- | ---: |")
                for k, v in data.items():
                    if isinstance(v, (int, float)):
                        lines.append(f"| {k.replace('_', ' ').title()} | {v:,.0f} |")
                    elif v is not None:
                        lines.append(f"| {k.replace('_', ' ').title()} | {v} |")
            lines.append("")
        return lines

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _flatten(obj: dict, prefix: str = "") -> dict[str, Any]:
        """Recursively flatten nested dicts for CSV output."""
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
            if isinstance(v, dict):
                out.update(OutputFormatter._flatten(v, key))
            elif isinstance(v, list):
                out[key] = json.dumps(v, default=str) if v else ""
            else:
                out[key] = v
        return out
