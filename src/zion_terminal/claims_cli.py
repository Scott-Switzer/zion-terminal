"""Read source-pinned reported equity claims without initializing financial providers."""

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import click

from zion_terminal.private_research import DEFAULT_BASE, emit_dataset, fetch, local_credentials


def validate_identifier(ctx, param, value):
    pattern = r"[0-9]{10}" if param.name == "cik" else r"[a-f0-9]{64}"
    if not re.fullmatch(pattern, value):
        raise click.BadParameter(
            "requires ten ASCII digits"
            if param.name == "cik"
            else "requires 64 lowercase hexadecimal characters"
        )
    return value


def validate_date(ctx, param, value):
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError()
        date.fromisoformat(value)
    except ValueError:
        raise click.BadParameter("requires a valid YYYY-MM-DD date") from None
    return value


@click.command()
@click.option("--cik", required=True, callback=validate_identifier)
@click.option(
    "--as-of",
    required=True,
    callback=validate_date,
    help="Availability cutoff date, interpreted at the end of its UTC day.",
)
@click.option(
    "--release-id",
    required=True,
    callback=validate_identifier,
    help="Approved immutable SEC engine release.",
)
@click.option(
    "--source-release-id",
    required=True,
    callback=validate_identifier,
    help="Approved immutable Company Facts companion release.",
)
@click.option("--base-url", help="Explicit HTTPS API origin; redirects are denied.")
@click.option(
    "--credential-file",
    type=click.Path(path_type=Path),
    default=Path.home() / ".config/zion/private-api.env",
    show_default=True,
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help="Export exact claims JSON including provenance; destination must be new.",
)
def claims(cik, as_of, release_id, source_release_id, base_url, credential_file, output):
    """Read filed equity claims with explicit engine, source and availability pins."""
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
        base,
        token,
        "equity-claims",
        {
            "cik": cik,
            "as_of": as_of,
            "release_id": release_id,
            "expected_release_id": release_id,
            "source_release_id": source_release_id,
            "expected_source_release_id": source_release_id,
        },
    )
    if (
        value.get("error") is not None
        or value.get("schema_version") != "zion-equity-claims-v1"
        or value.get("release_id") != release_id
        or value.get("source_release_id") != source_release_id
        or (value.get("source_bundle") or {}).get("issuer", {}).get("cik") != cik
    ):
        raise click.ClickException("CLAIMS_RESPONSE_CONTEXT_MISMATCH")
    emit_dataset(value, output)
