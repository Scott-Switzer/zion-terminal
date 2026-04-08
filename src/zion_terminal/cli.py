"""Zion Terminal CLI – click-based command interface.

Every flag that exists here actually works. No decorative flags.

Commands:
  zion query "Get AAPL stock price"   -- natural language query (no LLM required)
  zion quote AAPL                     -- direct quote lookup
  zion history AAPL --period 6mo --interval 1wk
  zion financials AAPL --statement income --quarterly
  zion filings AAPL --form 10-K --limit 5
  zion macro GDP
  zion info AAPL
  zion synthesis                      -- generate synthetic company data
"""

from __future__ import annotations

import logging
import sys

import click
from rich.console import Console

from zion_terminal import __version__
from zion_terminal.config.settings import get_settings
from zion_terminal.orchestrator.orchestrator import Orchestrator
from zion_terminal.outputs.formatter import OutputFormat, format_response
from zion_terminal.providers.base import build_provider

console = Console()


def _build_orchestrator() -> Orchestrator:
    """Build the orchestrator from current settings."""
    s = get_settings()
    llm = build_provider(
        provider_name=s.llm_provider,
        openai_api_key=s.openai_api_key,
        openai_model=s.openai_model,
        ollama_base_url=s.ollama_base_url,
        ollama_model=s.ollama_model,
    )
    return Orchestrator(
        fred_api_key=s.fred_api_key,
        edgar_identity=s.edgar_identity,
        llm=llm,
        cache_dir=s.cache_dir,
        cache_ttl=s.cache_ttl,
    )


_VALID_FORMATS = [f.value for f in OutputFormat]


def _validate_format(ctx: click.Context, param: click.Parameter, value: str) -> str:
    if value not in _VALID_FORMATS:
        raise click.BadParameter(
            f"Invalid format '{value}'. Must be one of: {', '.join(_VALID_FORMATS)}"
        )
    return value


# ── Root group ──────────────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="zion-terminal")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Zion Terminal – unified financial data retrieval."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s %(levelname)s: %(message)s")

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── zion query ──────────────────────────────────────────────────────────


@main.command()
@click.argument("text")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.option("--no-validate", is_flag=True, help="Skip validation checks.")
def query(text: str, fmt: str, no_validate: bool) -> None:
    """Run a natural-language financial query.

    Examples:
      zion query "Get AAPL stock price"
      zion query "Show me Tesla financials" --format json
    """
    orc = _build_orchestrator()
    try:
        resp = orc.query(text, validate=not no_validate)
        output = format_response(resp, OutputFormat(fmt))
        console.print(output)
        if not resp.success:
            sys.exit(1)
    finally:
        orc.close()


# ── zion quote ──────────────────────────────────────────────────────────


@main.command()
@click.argument("ticker")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
def quote(ticker: str, fmt: str) -> None:
    """Get a stock quote.

    Example: zion quote AAPL
    """
    orc = _build_orchestrator()
    try:
        resp = orc.query(f"{ticker.upper()} stock price")
        output = format_response(resp, OutputFormat(fmt))
        console.print(output)
        if not resp.success:
            sys.exit(1)
    finally:
        orc.close()


# ── zion history ────────────────────────────────────────────────────────


@main.command()
@click.argument("ticker")
@click.option("--period", "-p", default="1y", show_default=True,
              help="Time period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max")
@click.option("--interval", "-i", default="1d", show_default=True,
              help="Data interval: 1d, 5d, 1wk, 1mo")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
def history(ticker: str, period: str, interval: str, fmt: str) -> None:
    """Get historical price data.

    Example: zion history AAPL --period 6mo --interval 1wk
    """
    orc = _build_orchestrator()
    try:
        # Build tasks directly — bypass parser to honor exact CLI flags
        from zion_terminal.models.responses import OrchestratorResponse
        result = orc._retrieval.fetch([{
            "source": "yahoo_finance",
            "ticker": ticker.upper(),
            "action": "history",
            "period": period,
            "interval": interval,
        }])
        resp = OrchestratorResponse(
            success=result.success, query=f"{ticker} history {period} {interval}",
            intent="history", results=[result], errors=result.errors,
        )
        output = format_response(resp, OutputFormat(fmt))
        console.print(output)
        if not resp.success:
            sys.exit(1)
    finally:
        orc.close()


# ── zion financials ─────────────────────────────────────────────────────


@main.command()
@click.argument("ticker")
@click.option("--statement", "-s", default="income", show_default=True,
              type=click.Choice(["income", "balance", "cash_flow"]),
              help="Statement type.")
@click.option("--quarterly", "-q", is_flag=True, help="Get quarterly data instead of annual.")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
def financials(ticker: str, statement: str, quarterly: bool, fmt: str) -> None:
    """Get financial statements.

    Example: zion financials AAPL --statement balance --quarterly
    """
    orc = _build_orchestrator()
    try:
        from zion_terminal.models.responses import OrchestratorResponse
        result = orc._retrieval.fetch([{
            "source": "yahoo_finance",
            "ticker": ticker.upper(),
            "action": "financials",
            "statement_type": statement,
            "quarterly": quarterly,
        }])
        resp = OrchestratorResponse(
            success=result.success,
            query=f"{ticker} {statement} {'quarterly' if quarterly else 'annual'}",
            intent="financials", results=[result], errors=result.errors,
        )
        output = format_response(resp, OutputFormat(fmt))
        console.print(output)
        if not resp.success:
            sys.exit(1)
    finally:
        orc.close()


# ── zion filings ────────────────────────────────────────────────────────


@main.command()
@click.argument("ticker")
@click.option("--form", "-f", default=None,
              help="Filing type filter: 10-K, 10-Q, 8-K, etc.")
@click.option("--limit", "-l", default=10, show_default=True, type=int,
              help="Maximum number of filings to return.")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
def filings(ticker: str, form: str | None, limit: int, fmt: str) -> None:
    """Get SEC filings.

    Example: zion filings AAPL --form 10-K --limit 5
    """
    s = get_settings()
    if not s.edgar_identity:
        console.print("[bold red]Error:[/] EDGAR_IDENTITY not set. "
                       "SEC requires an identity string (e.g. 'Name email@example.com').\n"
                       "Set it in .env or export EDGAR_IDENTITY='...'")
        sys.exit(1)

    orc = _build_orchestrator()
    try:
        from zion_terminal.models.responses import OrchestratorResponse
        task: dict = {
            "source": "sec_edgar",
            "ticker": ticker.upper(),
            "action": "filings",
            "limit": limit,
        }
        if form:
            task["form"] = form

        result = orc._retrieval.fetch([task])
        resp = OrchestratorResponse(
            success=result.success,
            query=f"{ticker} filings form={form} limit={limit}",
            intent="filings", results=[result], errors=result.errors,
        )
        output = format_response(resp, OutputFormat(fmt))
        console.print(output)
        if not resp.success:
            sys.exit(1)
    finally:
        orc.close()


# ── zion macro ──────────────────────────────────────────────────────────


@main.command()
@click.argument("series")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
def macro(series: str, fmt: str) -> None:
    """Get macroeconomic data from FRED.

    Example: zion macro GDP
    """
    s = get_settings()
    if not s.fred_api_key:
        console.print("[bold red]Error:[/] FRED_API_KEY not set. "
                       "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")
        sys.exit(1)

    orc = _build_orchestrator()
    try:
        from zion_terminal.models.responses import OrchestratorResponse
        result = orc._retrieval.fetch([{"source": "fred", "series_id": series.upper()}])
        resp = OrchestratorResponse(
            success=result.success, query=f"FRED {series}",
            intent="macro", results=[result], errors=result.errors,
        )
        output = format_response(resp, OutputFormat(fmt))
        console.print(output)
        if not resp.success:
            sys.exit(1)
    finally:
        orc.close()


# ── zion info ───────────────────────────────────────────────────────────


@main.command()
@click.argument("ticker")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
def info(ticker: str, fmt: str) -> None:
    """Get company information.

    Example: zion info AAPL
    """
    orc = _build_orchestrator()
    try:
        from zion_terminal.models.responses import OrchestratorResponse
        result = orc._retrieval.fetch([{
            "source": "yahoo_finance",
            "ticker": ticker.upper(),
            "action": "info",
        }])
        resp = OrchestratorResponse(
            success=result.success, query=f"{ticker} company info",
            intent="company_info", results=[result], errors=result.errors,
        )
        output = format_response(resp, OutputFormat(fmt))
        console.print(output)
        if not resp.success:
            sys.exit(1)
    finally:
        orc.close()


# ── zion synthesis ──────────────────────────────────────────────────────


@main.command()
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
def synthesis(fmt: str) -> None:
    """Generate a synthetic company with consistent financials.

    Works without any LLM. With an LLM, also generates a press release.
    """
    orc = _build_orchestrator()
    try:
        resp = orc.query("generate synthetic company")
        output = format_response(resp, OutputFormat(fmt))
        console.print(output)
        if not resp.success:
            sys.exit(1)
    finally:
        orc.close()


if __name__ == "__main__":
    main()
