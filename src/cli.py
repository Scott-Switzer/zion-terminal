"""Zion Terminal CLI – command-line interface for financial data queries.

Usage:
    zion query "Pull NVDA's quarterly financials and the current Fed Funds rate"
    zion quote NVDA
    zion macro "fed funds rate"
    zion filings AAPL --form 10-K
    zion synthesize --ticker ACME
"""

from __future__ import annotations

import json
import logging
import sys

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from config.settings import get_settings
from src.orchestrator.orchestrator import Orchestrator
from src.outputs.formatter import OutputFormat, OutputFormatter

console = Console()
formatter = OutputFormatter()


def _get_orchestrator() -> Orchestrator:
    """Build an Orchestrator from environment settings."""
    settings = get_settings()
    return Orchestrator(
        fred_api_key=settings.fred_api_key,
        edgar_identity=settings.edgar_identity,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_model,
        cache_dir=settings.cache_dir,
        cache_ttl=settings.cache_ttl,
    )


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
def cli(debug: bool) -> None:
    """Zion Terminal – unified financial data retrieval."""
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@cli.command()
@click.argument("text")
@click.option(
    "--format", "-f",
    "output_format",
    type=click.Choice(["markdown", "csv", "json"]),
    default="markdown",
    help="Output format",
)
@click.option("--no-validate", is_flag=True, help="Skip validation checks")
def query(text: str, output_format: str, no_validate: bool) -> None:
    """Run a natural-language financial data query."""
    orch = _get_orchestrator()
    try:
        with console.status("[bold green]Processing query…"):
            response = orch.query(text, validate=not no_validate)

        fmt = OutputFormat(output_format)
        output = formatter.format(response, fmt)

        if fmt == OutputFormat.MARKDOWN:
            console.print(Markdown(output))
        elif fmt == OutputFormat.JSON:
            console.print_json(output)
        else:
            console.print(output)

        if not response.success:
            sys.exit(1)
    finally:
        orch.close()


@cli.command()
@click.argument("ticker")
@click.option("--format", "-f", "output_format", default="markdown")
def quote(ticker: str, output_format: str) -> None:
    """Get a stock quote."""
    orch = _get_orchestrator()
    try:
        with console.status(f"[bold green]Fetching quote for {ticker}…"):
            response = orch.query(f"Get current stock price for {ticker}")

        fmt = OutputFormat(output_format)
        output = formatter.format(response, fmt)
        if fmt == OutputFormat.MARKDOWN:
            console.print(Markdown(output))
        else:
            console.print(output)
    finally:
        orch.close()


@cli.command()
@click.argument("ticker")
@click.option("--statement", "-s", type=click.Choice(["income", "balance", "cash_flow"]), default="income")
@click.option("--quarterly", "-q", is_flag=True, help="Quarterly instead of annual")
@click.option("--format", "-f", "output_format", default="markdown")
def financials(ticker: str, statement: str, quarterly: bool, output_format: str) -> None:
    """Get financial statements for a company."""
    orch = _get_orchestrator()
    try:
        q = "quarterly" if quarterly else "annual"
        stmt_name = statement.replace("_", " ")
        with console.status(f"[bold green]Fetching {q} {stmt_name} for {ticker}…"):
            response = orch.query(
                f"Pull {ticker}'s {q} {stmt_name}"
            )

        fmt = OutputFormat(output_format)
        output = formatter.format(response, fmt)
        if fmt == OutputFormat.MARKDOWN:
            console.print(Markdown(output))
        else:
            console.print(output)
    finally:
        orch.close()


@cli.command()
@click.argument("indicator")
@click.option("--format", "-f", "output_format", default="markdown")
def macro(indicator: str, output_format: str) -> None:
    """Get a macroeconomic indicator from FRED."""
    orch = _get_orchestrator()
    try:
        with console.status(f"[bold green]Fetching {indicator}…"):
            response = orch.query(f"Get the current {indicator}")

        fmt = OutputFormat(output_format)
        output = formatter.format(response, fmt)
        if fmt == OutputFormat.MARKDOWN:
            console.print(Markdown(output))
        else:
            console.print(output)
    finally:
        orch.close()


@cli.command()
@click.argument("ticker")
@click.option("--form", type=click.Choice(["10-K", "10-Q", "8-K"]), default=None)
@click.option("--limit", "-n", default=10)
@click.option("--format", "-f", "output_format", default="markdown")
def filings(ticker: str, form: str | None, limit: int, output_format: str) -> None:
    """List SEC filings for a company."""
    orch = _get_orchestrator()
    try:
        q = f"List {ticker}'s SEC filings"
        if form:
            q += f" (form {form})"
        with console.status(f"[bold green]Fetching filings for {ticker}…"):
            response = orch.query(q)

        fmt = OutputFormat(output_format)
        output = formatter.format(response, fmt)
        if fmt == OutputFormat.MARKDOWN:
            console.print(Markdown(output))
        else:
            console.print(output)
    finally:
        orch.close()


@cli.command()
@click.option("--ticker", "-t", default=None, help="Custom ticker for the synthetic entity")
@click.option("--format", "-f", "output_format", default="markdown")
def synthesize(ticker: str | None, output_format: str) -> None:
    """Generate a synthetic company with consistent financials."""
    orch = _get_orchestrator()
    try:
        q = "Generate a synthetic company with financial statements"
        if ticker:
            q += f" using ticker {ticker}"
        with console.status("[bold green]Generating synthetic entity…"):
            response = orch.query(q)

        fmt = OutputFormat(output_format)
        output = formatter.format(response, fmt)
        if fmt == OutputFormat.MARKDOWN:
            console.print(Markdown(output))
        else:
            console.print(output)
    finally:
        orch.close()


@cli.command()
@click.argument("ticker")
@click.option("--period", "-p", default="1y", help="Period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max")
@click.option("--interval", "-i", default="1d", help="Interval: 1d, 1wk, 1mo")
@click.option("--format", "-f", "output_format", default="markdown")
def history(ticker: str, period: str, interval: str, output_format: str) -> None:
    """Get historical price data."""
    orch = _get_orchestrator()
    try:
        with console.status(f"[bold green]Fetching history for {ticker}…"):
            response = orch.query(f"Show historical prices for {ticker}")

        fmt = OutputFormat(output_format)
        output = formatter.format(response, fmt)
        if fmt == OutputFormat.MARKDOWN:
            console.print(Markdown(output))
        else:
            console.print(output)
    finally:
        orch.close()


@cli.command()
def sources() -> None:
    """List available data sources."""
    orch = _get_orchestrator()
    try:
        srcs = orch.available_sources
        console.print(Panel.fit(
            "\n".join(f"  • {s}" for s in srcs),
            title="Available Data Sources",
            border_style="green",
        ))
    finally:
        orch.close()


def main() -> None:
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
