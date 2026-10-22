"""Direct, private stock-history access; no LLM or upstream provider is initialized."""

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import click

from zion_terminal.private_research import DEFAULT_BASE, emit_dataset, fetch, local_credentials


@click.command()
@click.option(
    "--source",
    type=click.Choice(
        [
            "crsp",
            "alpaca_latest",
            "alpaca",
            "yfinance",
            "simfin",
            "tiingo",
            "eodhd",
            "marketstack",
            "polygon",
            "twelve_data",
            "estate_alpaca",
        ]
    ),
)
@click.option("--security-id")
@click.option("--permno", type=click.IntRange(1, 9999999999))
@click.option("--symbol")
@click.option("--search")
@click.option("--date-from")
@click.option("--date-to")
@click.option("--expected-release-id")
@click.option("--cursor")
@click.option("--limit", type=click.IntRange(1, 500), default=100)
@click.option("--base-url")
@click.option(
    "--credential-file",
    type=click.Path(path_type=Path),
    default=Path.home() / ".config/zion/private-api.env",
)
@click.option("--output", type=click.Path(path_type=Path))
def stocks(base_url, credential_file, output, **params):
    """Search CRSP history by default, or select the ongoing Alpaca source.

    Use PERMNO for an unambiguous CRSP security across historical ticker changes.

    Observation dates are not publication dates. Historical as-of is intentionally unavailable.
    """
    if params["permno"] and (params["symbol"] or params["source"] not in {None, "crsp"}):
        raise click.ClickException("PERMNO_REQUIRES_CRSP_WITHOUT_SYMBOL")
    if params["security_id"] and (
        params["source"]
        not in {
            "polygon",
            "eodhd",
            "simfin",
            "tiingo",
            "marketstack",
            "yfinance",
            "estate_alpaca",
            "twelve_data",
        }
        or params["permno"]
        or params["symbol"]
        or not re.fullmatch(r"[0-9]{1,20}", params["security_id"])
    ):
        raise click.ClickException("SECURITY_ID_REQUIRES_EXPLICIT_ARCHIVE_PROVIDER")
    if params["symbol"] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,24}", params["symbol"]):
        raise click.ClickException("INVALID_STOCK_SYMBOL")
    for key in ("date_from", "date_to"):
        if params[key]:
            try:
                if date.fromisoformat(params[key]).isoformat() != params[key]:
                    raise ValueError()
            except ValueError:
                raise click.ClickException("INVALID_DATE") from None
    pin = params["expected_release_id"]
    if pin and not re.fullmatch(r"[a-f0-9]{64}", pin):
        raise click.ClickException("INVALID_RELEASE_PIN")
    try:
        credentials = local_credentials(credential_file)
    except (OSError, UnicodeError):
        raise click.ClickException("CREDENTIAL_FILE_UNREADABLE") from None
    token = os.environ.get("ZION_PRIVATE_API_TOKEN") or credentials.get("ZION_PRIVATE_API_TOKEN")
    base = (
        base_url
        or os.environ.get("ZION_PRIVATE_BASE_URL")
        or credentials.get("ZION_PRIVATE_BASE_URL")
        or DEFAULT_BASE
    ).rstrip("/")
    parsed = urlparse(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise click.ClickException("BASE_URL_REQUIRES_CREDENTIAL_FREE_HTTPS_ORIGIN")
    if not token or any(char in token for char in "\r\n"):
        raise click.ClickException("PRIVATE_API_CREDENTIAL_REQUIRED")
    value, _ = fetch(
        base, token, "stock-history", {k: v for k, v in params.items() if v is not None}
    )
    if value.get("schema_version") != "zion-stock-history-v1":
        raise click.ClickException("UNSUPPORTED_STOCK_HISTORY_CONTRACT")
    if pin and value.get("release_id") != pin:
        raise click.ClickException("STOCK_HISTORY_RELEASE_MISMATCH")
    emit_dataset(value, output)
