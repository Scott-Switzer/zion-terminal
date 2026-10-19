"""Direct owner-only dataset consumers; no LLM, normalization or provider calls."""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import click

from zion_terminal.private_research import DEFAULT_BASE, emit_dataset, fetch, local_credentials


def relative_path(value, *, empty=False):
    if (not isinstance(value, str) or (not empty and not value) or len(value) > 1024
            or value.startswith("/") or "://" in value
            or re.search(r"[\\\x00-\x1f\x7f]", value)
            or any(part in (".", "..") for part in value.split("/"))):
        raise click.ClickException("INVALID_DATASET_PATH")
    return value


def request_options(action, *, dataset_id, store, prefix, path, columns, offset, limit,
                    cursor, search, family):
    """Return one exact existing route; refuse irrelevant options or mixed pagination."""
    allowed = {
        "catalog": {"search", "family", "offset", "limit"},
        "describe": {"dataset_id"},
        "objects": {"store", "prefix", "limit", "cursor"},
        "schema": {"store", "path"},
        "rows": {"store", "path", "columns", "offset", "limit", "cursor"},
    }
    supplied = {
        "dataset_id": dataset_id, "store": store, "prefix": prefix, "path": path,
        "columns": columns, "offset": offset, "limit": limit, "cursor": cursor,
        "search": search, "family": family,
    }
    if any(value is not None and key not in allowed[action] for key, value in supplied.items()):
        raise click.ClickException("OPTION_NOT_APPLICABLE_TO_DATASET_ACTION")
    if cursor is not None and (not cursor or len(cursor) > 8192):
        raise click.ClickException("INVALID_CURSOR")
    if action == "describe":
        if not dataset_id or not re.fullmatch(r"[a-f0-9]{32}", dataset_id):
            raise click.ClickException("INVALID_DATASET_ID")
        return "datasets/catalog/" + dataset_id, {}
    if action == "catalog":
        if any(value is not None and len(value) > 128 for value in (search, family)):
            raise click.ClickException("INVALID_CATALOG_FILTER")
        params = {"offset": offset if offset is not None else 0, "limit": limit or 50}
        if search is not None:
            params["search"] = search
        if family is not None:
            params["family"] = family
        route = "datasets/catalog"
    else:
        if store is None:
            raise click.ClickException("DATASET_STORE_REQUIRED")
        route = "datasets/" + store + "/" + action
        if action == "objects":
            params = {"prefix": relative_path(prefix or "", empty=True), "limit": limit or 50}
        else:
            params = {"path": relative_path(path)}
        if action == "rows":
            params["limit"] = limit or 50
            if cursor is not None and offset is not None:
                raise click.ClickException("CURSOR_AND_OFFSET_ARE_EXCLUSIVE")
            if cursor is None:
                params["offset"] = offset if offset is not None else 0
            if columns is not None:
                selected = columns.split(",")
                if (len(selected) > 256 or len(set(selected)) != len(selected)
                        or any(not c or len(c) > 256 for c in selected)):
                    raise click.ClickException("INVALID_COLUMNS")
                params["columns"] = columns
        if cursor is not None:
            params["cursor"] = cursor
    if action in ("catalog", "objects") and params["limit"] > 200:
        raise click.ClickException("DATASET_LIST_LIMIT_MAX_200")
    return route, params


@click.command()
@click.argument("action", type=click.Choice(["catalog", "describe", "objects", "schema", "rows"]),
                required=False, default="catalog")
@click.option("--dataset-id", help="Exact 32-character ID from the native catalog.")
@click.option("--search")
@click.option("--family")
@click.option("--store", type=click.Choice(["financial", "research", "filings", "lakehouse"]))
@click.option("--prefix")
@click.option("--path")
@click.option("--columns", help="Comma-separated exact native column names; no normalization.")
@click.option("--offset", type=click.IntRange(0, 9007199254740991))
@click.option("--limit", type=click.IntRange(1, 1000), help="Default 50; listings max 200.")
@click.option("--cursor", help="Opaque continuation returned by the same source request.")
@click.option("--base-url", help="Credential-free HTTPS API origin; redirects are refused.")
@click.option("--credential-file", type=click.Path(path_type=Path),
              default=Path.home() / ".config/zion/private-api.env")
@click.option("--output", type=click.Path(path_type=Path),
              help="Also save exact native JSON; destination must be new.")
def datasets(action, base_url, credential_file, output, **options):
    """Search or read existing private datasets using their native source fields.

    Defaults to catalog. Schema and row requests use a path obtained from describe
    or objects. Responses retain source versions, units, precision and nulls.
    Native access does not establish financial correctness or historical availability.
    """
    route, params = request_options(action, **options)
    try:
        credentials = local_credentials(credential_file)
    except (OSError, UnicodeError):
        raise click.ClickException("CREDENTIAL_FILE_UNREADABLE") from None
    token = os.environ.get("ZION_PRIVATE_API_TOKEN") or credentials.get("ZION_PRIVATE_API_TOKEN")
    base = (base_url or os.environ.get("ZION_PRIVATE_BASE_URL")
            or credentials.get("ZION_PRIVATE_BASE_URL") or DEFAULT_BASE).rstrip("/")
    try:
        parsed = urlparse(base)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.path and not parsed.query
                 and not parsed.fragment and not re.search(r"[\x00-\x20\x7f]", base))
        parsed.port  # Refuse malformed or out-of-range ports before creating any request.
    except ValueError:
        valid = False
    if not valid:
        raise click.ClickException("BASE_URL_REQUIRES_CREDENTIAL_FREE_HTTPS_ORIGIN")
    if not token or re.search(r"[\x00-\x20\x7f]", token):
        raise click.ClickException("PRIVATE_API_CREDENTIAL_REQUIRED")
    value, _ = fetch(base, token, route, params)
    emit_dataset(value, output)
