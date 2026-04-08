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
  zion filing-markdown AAPL --form 10-K  -- fetch filing as markdown (experimental)
  zion company-facts AAPL               -- fetch XBRL company facts
  zion synthesis                        -- generate synthetic company data
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


def _build_orchestrator(strict: bool = False) -> Orchestrator:
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
        strict=strict,
    )


_VALID_FORMATS = [f.value for f in OutputFormat]


def _validate_format(ctx: click.Context, param: click.Parameter, value: str) -> str:
    if value not in _VALID_FORMATS:
        raise click.BadParameter(
            f"Invalid format '{value}'. Must be one of: {', '.join(_VALID_FORMATS)}"
        )
    return value


def _run_and_output(orc: Orchestrator, resp, fmt: str) -> None:
    """Format and print response, exit non-zero on failure."""
    output = format_response(resp, OutputFormat(fmt))
    console.print(output)
    if not resp.success:
        sys.exit(1)


# ── Root group ──────────────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="zion-terminal")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.option("--strict", is_flag=True, help="Fail on validation errors (strict mode).")
@click.pass_context
def main(ctx: click.Context, verbose: bool, strict: bool) -> None:
    """Zion Terminal – unified financial data retrieval."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s %(levelname)s: %(message)s")
    ctx.ensure_object(dict)
    ctx.obj["strict"] = strict

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── zion query ──────────────────────────────────────────────────────────


@main.command()
@click.argument("text")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.option("--no-validate", is_flag=True, help="Skip validation checks.")
@click.pass_context
def query(ctx: click.Context, text: str, fmt: str, no_validate: bool) -> None:
    """Run a natural-language financial query.

    Examples:
      zion query "Get AAPL stock price"
      zion query "Show me Tesla financials" --format json
    """
    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.query(text, validate=not no_validate)
        _run_and_output(orc, resp, fmt)
    finally:
        orc.close()


# ── zion quote ──────────────────────────────────────────────────────────


@main.command()
@click.argument("ticker")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.pass_context
def quote(ctx: click.Context, ticker: str, fmt: str) -> None:
    """Get a stock quote.

    Example: zion quote AAPL
    """
    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.get_quote(ticker)
        _run_and_output(orc, resp, fmt)
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
@click.pass_context
def history(ctx: click.Context, ticker: str, period: str, interval: str, fmt: str) -> None:
    """Get historical price data.

    Example: zion history AAPL --period 6mo --interval 1wk
    """
    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.get_history(ticker, period=period, interval=interval)
        _run_and_output(orc, resp, fmt)
    finally:
        orc.close()


# ── zion financials ─────────────────────────────────────────────────────


@main.command()
@click.argument("ticker")
@click.option("--statement", "-s", default="income", show_default=True,
              type=click.Choice(["income", "balance", "cash_flow"]),
              help="Statement type.")
@click.option("--quarterly", "-q", is_flag=True, help="Get quarterly data instead of annual.")
@click.option("--source", default="sec", show_default=True,
              type=click.Choice(["sec", "yahoo"]),
              help="Data source: sec (primary) or yahoo (fallback).")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.pass_context
def financials(ctx: click.Context, ticker: str, statement: str, quarterly: bool, source: str, fmt: str) -> None:
    """Get financial statements.

    SEC EDGAR is the primary source. Yahoo Finance is a fallback.
    Example: zion financials AAPL --statement balance --quarterly
    """
    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.get_financials(ticker, statement_type=statement, quarterly=quarterly, source=source)
        _run_and_output(orc, resp, fmt)
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
@click.pass_context
def filings(ctx: click.Context, ticker: str, form: str | None, limit: int, fmt: str) -> None:
    """Get SEC filings.

    Example: zion filings AAPL --form 10-K --limit 5
    """
    s = get_settings()
    if not s.edgar_identity:
        console.print("[bold red]Error:[/] EDGAR_IDENTITY not set. "
                       "SEC requires an identity string (e.g. 'Name email@example.com').\n"
                       "Set it in .env or export EDGAR_IDENTITY='...'")
        sys.exit(1)

    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.get_filings(ticker, form=form, limit=limit)
        _run_and_output(orc, resp, fmt)
    finally:
        orc.close()


# ── zion macro ──────────────────────────────────────────────────────────


@main.command()
@click.argument("series")
@click.option("--start", "start_date", default=None, help="Start date (YYYY-MM-DD).")
@click.option("--end", "end_date", default=None, help="End date (YYYY-MM-DD).")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.pass_context
def macro(ctx: click.Context, series: str, start_date: str | None, end_date: str | None,
          fmt: str) -> None:
    """Get macroeconomic data from FRED.

    Example: zion macro GDP
    """
    s = get_settings()
    if not s.fred_api_key:
        console.print("[bold red]Error:[/] FRED_API_KEY not set. "
                       "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")
        sys.exit(1)

    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.get_macro(series, start_date=start_date, end_date=end_date)
        _run_and_output(orc, resp, fmt)
    finally:
        orc.close()


# ── zion info ───────────────────────────────────────────────────────────


@main.command()
@click.argument("ticker")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.pass_context
def info(ctx: click.Context, ticker: str, fmt: str) -> None:
    """Get company information.

    Example: zion info AAPL
    """
    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.get_info(ticker)
        _run_and_output(orc, resp, fmt)
    finally:
        orc.close()


# ── zion filing-markdown ────────────────────────────────────────────────


@main.command("filing-markdown")
@click.argument("ticker")
@click.option("--form", "-f", default="10-K", show_default=True,
              help="Filing type: 10-K, 10-Q, 8-K, etc.")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.pass_context
def filing_markdown(ctx: click.Context, ticker: str, form: str, fmt: str) -> None:
    """Fetch an SEC filing and convert to markdown (experimental).

    Fetches the most recent filing of the given type and converts
    the primary document from HTML to markdown.

    Example: zion filing-markdown AAPL --form 10-K
    """
    s = get_settings()
    if not s.edgar_identity:
        console.print("[bold red]Error:[/] EDGAR_IDENTITY not set. "
                       "SEC requires an identity string (e.g. 'Name email@example.com').\n"
                       "Set it in .env or export EDGAR_IDENTITY='...'")
        sys.exit(1)

    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.get_filing_markdown(ticker, form=form)
        _run_and_output(orc, resp, fmt)
    finally:
        orc.close()


# ── zion company-facts ──────────────────────────────────────────────────


@main.command("company-facts")
@click.argument("ticker")
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.pass_context
def company_facts(ctx: click.Context, ticker: str, fmt: str) -> None:
    """Fetch XBRL company facts from SEC EDGAR.

    Returns structured XBRL financial facts reported by the company.

    Example: zion company-facts AAPL
    """
    s = get_settings()
    if not s.edgar_identity:
        console.print("[bold red]Error:[/] EDGAR_IDENTITY not set. "
                       "SEC requires an identity string (e.g. 'Name email@example.com').\n"
                       "Set it in .env or export EDGAR_IDENTITY='...'")
        sys.exit(1)

    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.get_company_facts(ticker)
        _run_and_output(orc, resp, fmt)
    finally:
        orc.close()


# ── zion synthesis ──────────────────────────────────────────────────────


@main.command()
@click.option("--format", "fmt", default="markdown", callback=_validate_format,
              help=f"Output format: {', '.join(_VALID_FORMATS)}")
@click.pass_context
def synthesis(ctx: click.Context, fmt: str) -> None:
    """Generate a synthetic company with consistent financials.

    Works without any LLM. With an LLM, also generates a press release.
    """
    orc = _build_orchestrator(strict=ctx.obj.get("strict", False))
    try:
        resp = orc.query("generate synthetic company")
        _run_and_output(orc, resp, fmt)
    finally:
        orc.close()


if __name__ == "__main__":
    main()
