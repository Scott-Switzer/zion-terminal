"""Output formatting – markdown, JSON, CSV.

All formatters operate on OrchestratorResponse and its nested models.
Markdown tables are valid (header + separator + rows). JSON uses a stable schema.
CSV is RFC 4180 compliant.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from enum import Enum
from typing import Any

from zion_terminal.models.responses import (
    AgentResponse,
    OrchestratorResponse,
    RetrievalResult,
    SynthesisResult,
    ValidationResult,
    ValidationStatus,
)

logger = logging.getLogger(__name__)


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"


# ── Public entry point ──────────────────────────────────────────────────


def format_response(response: OrchestratorResponse, fmt: OutputFormat = OutputFormat.MARKDOWN) -> str:
    """Format an OrchestratorResponse in the requested format."""
    if fmt == OutputFormat.JSON:
        return _to_json(response)
    elif fmt == OutputFormat.CSV:
        return _to_csv(response)
    else:
        return _to_markdown(response)


# ── JSON formatter ──────────────────────────────────────────────────────


def _to_json(response: OrchestratorResponse) -> str:
    """Stable JSON schema output."""
    payload: dict[str, Any] = {
        "success": response.success,
        "query": response.query,
        "intent": response.intent,
        "errors": response.errors,
        "data": [],
        "validation": None,
        "metadata": response.metadata,
    }

    for r in response.results:
        if isinstance(r, RetrievalResult):
            payload["data"].extend(r.data)
        elif isinstance(r, SynthesisResult):
            payload["data"].extend(r.documents)
        elif isinstance(r, ValidationResult):
            payload["validation"] = {
                "status": r.status.value,
                "checks_run": r.checks_run,
                "checks_passed": r.checks_passed,
                "checks_failed": r.checks_failed,
                "details": [
                    {
                        "check": c.check_name,
                        "status": c.status.value,
                        "message": c.message,
                    }
                    for c in r.details
                ],
            }

    return json.dumps(payload, indent=2, default=str)


# ── CSV formatter ───────────────────────────────────────────────────────


def _to_csv(response: OrchestratorResponse) -> str:
    """Flatten data into CSV. Best-effort for heterogeneous results."""
    rows = response.all_data
    if not rows:
        return ""

    # Collect all unique keys across all rows (order-preserving)
    all_keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            for k in row:
                if k not in seen:
                    all_keys.append(k)
                    seen.add(k)

    if not all_keys:
        return ""

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_keys, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        if isinstance(row, dict):
            # Stringify complex nested values
            flat = {}
            for k, v in row.items():
                if isinstance(v, (dict, list)):
                    flat[k] = json.dumps(v, default=str)
                else:
                    flat[k] = v
            writer.writerow(flat)

    return output.getvalue()


# ── Markdown formatter ──────────────────────────────────────────────────


def _to_markdown(response: OrchestratorResponse) -> str:
    """Human-readable markdown with valid tables."""
    parts: list[str] = []

    if not response.success:
        parts.append("## Errors\n")
        for err in response.errors:
            parts.append(f"- {err}")
        parts.append("")

    for r in response.results:
        if isinstance(r, RetrievalResult):
            parts.append(_format_retrieval_md(r))
        elif isinstance(r, SynthesisResult):
            parts.append(_format_synthesis_md(r))
        elif isinstance(r, ValidationResult):
            parts.append(_format_validation_md(r))

    return "\n".join(parts).strip()


def _format_retrieval_md(result: RetrievalResult) -> str:
    """Format retrieval results as markdown."""
    parts: list[str] = []

    if result.errors:
        for err in result.errors:
            parts.append(f"> **Warning**: {err}\n")

    for item in result.data:
        if not isinstance(item, dict):
            continue

        # Stock quote
        if "price" in item and "ticker" in item:
            parts.append(_format_quote_md(item))
        # Time series
        elif "data_points" in item:
            parts.append(_format_timeseries_md(item))
        # Financial statement
        elif "line_items" in item:
            parts.append(_format_statement_md(item))
        # SEC filing
        elif "filing_type" in item:
            parts.append(_format_filing_md(item))
        # Company info
        elif item.get("type") == "company_info":
            parts.append(_format_company_info_md(item))
        # Macro indicator
        elif "series_id" in item:
            parts.append(_format_macro_md(item))
        else:
            parts.append(f"```json\n{json.dumps(item, indent=2, default=str)}\n```\n")

    if result.sources_used:
        parts.append(f"\n*Sources: {', '.join(result.sources_used)}*")
    if result.cached:
        parts.append("*(cached)*")

    return "\n".join(parts)


def _format_quote_md(item: dict) -> str:
    ticker = item.get("ticker", "?")
    name = item.get("company_name", "")
    header = f"## {ticker}" + (f" — {name}" if name else "")
    rows = []
    field_labels = [
        ("price", "Price"), ("open", "Open"), ("high", "High"), ("low", "Low"),
        ("close", "Prev Close"), ("volume", "Volume"), ("market_cap", "Market Cap"),
        ("pe_ratio", "P/E Ratio"), ("dividend_yield", "Div Yield"),
        ("fifty_two_week_high", "52W High"), ("fifty_two_week_low", "52W Low"),
    ]
    for key, label in field_labels:
        val = item.get(key)
        if val is not None:
            if key in ("market_cap",) and isinstance(val, (int, float)):
                rows.append(f"| {label} | ${val:,.0f} |")
            elif key in ("price", "open", "high", "low", "close", "fifty_two_week_high", "fifty_two_week_low"):
                rows.append(f"| {label} | ${val:,.2f} |")
            elif key == "volume" and isinstance(val, (int, float)):
                rows.append(f"| {label} | {val:,.0f} |")
            elif key == "dividend_yield" and isinstance(val, (int, float)):
                rows.append(f"| {label} | {val:.2%} |")
            elif key == "pe_ratio" and isinstance(val, (int, float)):
                rows.append(f"| {label} | {val:.2f} |")
            else:
                rows.append(f"| {label} | {val} |")

    if not rows:
        return header + "\n\nNo data available.\n"

    table = f"{header}\n\n| Metric | Value |\n|--------|-------|\n" + "\n".join(rows) + "\n"
    return table


def _format_timeseries_md(item: dict) -> str:
    name = item.get("name", "Time Series")
    data_points = item.get("data_points", [])
    if not data_points:
        return f"## {name}\n\nNo data points.\n"

    # Show first and last few rows
    max_rows = 20
    show = data_points[:max_rows]
    truncated = len(data_points) > max_rows

    # Build table from keys of first data point
    cols = list(show[0].keys()) if show else []
    if not cols:
        return f"## {name}\n\nNo data points.\n"

    header_row = "| " + " | ".join(cols) + " |"
    sep_row = "|" + "|".join(["------" for _ in cols]) + "|"
    data_rows = []
    for dp in show:
        vals = []
        for c in cols:
            v = dp.get(c, "")
            if isinstance(v, float):
                vals.append(f"{v:,.4f}")
            elif isinstance(v, int):
                vals.append(f"{v:,}")
            else:
                vals.append(str(v))
        data_rows.append("| " + " | ".join(vals) + " |")

    table = f"## {name}\n\n{header_row}\n{sep_row}\n" + "\n".join(data_rows)
    if truncated:
        table += f"\n\n*Showing {max_rows} of {len(data_points)} rows.*"
    return table + "\n"


def _format_statement_md(item: dict) -> str:
    ticker = item.get("ticker", "?")
    stype = item.get("statement_type", "financial_statement")
    period = item.get("period", "")
    line_items = item.get("line_items", {})

    header = f"## {ticker} — {_humanize(stype)} ({period})"
    if not line_items:
        return header + "\n\nNo data.\n"

    rows = []
    for k, v in line_items.items():
        if v is not None:
            rows.append(f"| {k} | {v:,.0f} |")
        else:
            rows.append(f"| {k} | — |")

    return f"{header}\n\n| Line Item | Value |\n|-----------|-------|\n" + "\n".join(rows) + "\n"


def _format_filing_md(item: dict) -> str:
    ftype = item.get("filing_type", "unknown")
    fdate = item.get("filing_date", "")
    url = item.get("document_url", "")
    desc = item.get("description", "")
    md = item.get("content_markdown", "")

    line = f"- **{ftype}** ({fdate})"
    if desc:
        line += f" — {desc}"
    if url:
        line += f" [link]({url})"
    if md:
        line += f"\n\n<details><summary>Filing content (markdown)</summary>\n\n{md[:5000]}\n\n</details>"
    return line


def _format_company_info_md(item: dict) -> str:
    ticker = item.get("ticker", "?")
    data = item.get("data", {})
    if not data:
        return f"## {ticker} — Company Info\n\nNo data available.\n"

    # Pick key fields
    important = [
        "shortName", "longName", "sector", "industry", "country", "city",
        "fullTimeEmployees", "website", "longBusinessSummary",
    ]
    rows = []
    for k in important:
        v = data.get(k)
        if v is not None:
            rows.append(f"| {_humanize(k)} | {v} |")

    if not rows:
        return f"## {ticker} — Company Info\n\n```json\n{json.dumps(data, indent=2, default=str)[:3000]}\n```\n"

    return f"## {ticker} — Company Info\n\n| Field | Value |\n|-------|-------|\n" + "\n".join(rows) + "\n"


def _format_macro_md(item: dict) -> str:
    sid = item.get("series_id", "?")
    title = item.get("title", sid)
    value = item.get("value")
    obs_date = item.get("observation_date", "")
    dps = item.get("data_points", [])

    parts = [f"## {title} ({sid})"]
    if value is not None:
        parts.append(f"\nLatest: **{value}** (as of {obs_date})\n")

    if dps:
        max_rows = 20
        show = dps[:max_rows]
        cols = list(show[0].keys()) if show else []
        if cols:
            header_row = "| " + " | ".join(cols) + " |"
            sep_row = "|" + "|".join(["------" for _ in cols]) + "|"
            data_rows = []
            for dp in show:
                vals = [str(dp.get(c, "")) for c in cols]
                data_rows.append("| " + " | ".join(vals) + " |")
            parts.append(f"\n{header_row}\n{sep_row}\n" + "\n".join(data_rows))
            if len(dps) > max_rows:
                parts.append(f"\n*Showing {max_rows} of {len(dps)} rows.*")

    return "\n".join(parts) + "\n"


def _format_synthesis_md(result: SynthesisResult) -> str:
    parts = [f"## Synthetic Data — {result.entity_name or 'Unknown'} ({result.entity_ticker or '?'})\n"]
    for doc in result.documents:
        doc_type = doc.get("type", "unknown")
        data = doc.get("data", {})
        parts.append(f"### {_humanize(doc_type)}\n")
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, (int, float)):
                    parts.append(f"- **{_humanize(k)}**: {v:,.0f}")
                elif v is not None:
                    parts.append(f"- **{_humanize(k)}**: {v}")
        elif isinstance(data, str):
            parts.append(data)
        parts.append("")
    return "\n".join(parts)


def _format_validation_md(result: ValidationResult) -> str:
    icon = {"passed": "PASS", "failed": "FAIL", "warning": "WARN"}
    parts = [
        f"## Validation — {icon.get(result.status.value, result.status.value)}",
        f"\nChecks: {result.checks_run} total, {result.checks_passed} passed, {result.checks_failed} failed\n",
    ]
    if result.details:
        parts.append("| Check | Status | Message |")
        parts.append("|-------|--------|---------|")
        for c in result.details:
            parts.append(f"| {c.check_name} | {c.status.value} | {c.message} |")
    parts.append("")
    return "\n".join(parts)


def _humanize(s: str) -> str:
    """Convert snake_case or camelCase to Title Case."""
    import re as _re
    s = _re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    return s.replace("_", " ").title()
