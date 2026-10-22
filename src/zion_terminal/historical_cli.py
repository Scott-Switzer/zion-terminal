"""Direct, private historical-series access; no LLM or upstream provider is initialized."""

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import click

from zion_terminal.private_research import DEFAULT_BASE, emit_dataset, fetch, local_credentials


@click.command()
@click.option("--series-id")
@click.option("--search")
@click.option("--asset-class")
@click.option("--date-from")
@click.option("--date-to")
@click.option("--expected-release-id")
@click.option("--cursor")
@click.option("--limit", type=click.IntRange(1, 500), default=100)
@click.option("--view", type=click.Choice(["catalog", "sources"]), default="catalog")
@click.option("--base-url")
@click.option(
    "--credential-file",
    type=click.Path(path_type=Path),
    default=Path.home() / ".config/zion/private-api.env",
)
@click.option("--output", type=click.Path(path_type=Path))
def historical(base_url, credential_file, output, **params):
    """Discover historical series or retrieve dated, source-qualified observations.

    Observation dates are not publication dates. Historical as-of is intentionally unavailable.
    """
    if params["series_id"] and not re.fullmatch(r"[A-Za-z0-9_:.-]{1,180}", params["series_id"]):
        raise click.ClickException("INVALID_SERIES_ID")
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
        base, token, "historical-series", {k: v for k, v in params.items() if v is not None}
    )
    if value.get("schema_version") != "zion-historical-series-v1":
        raise click.ClickException("UNSUPPORTED_HISTORICAL_SERIES_CONTRACT")
    if pin and value.get("release_id") != pin:
        raise click.ClickException("HISTORICAL_SERIES_RELEASE_MISMATCH")
    emit_dataset(value, output)
