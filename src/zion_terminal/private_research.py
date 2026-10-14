"""Private SEC research client; standalone execution makes no provider or LLM calls."""

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import click

DEFAULT_BASE = "https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev"
MAX_RESPONSE = 2 * 1048576
MAX_TOTAL = 20 * 1048576
ROUTES = {
    "companies": "financial-companies",
    "history": "financial-history",
    "batch": "financial-history/batch",
    "screen": "financial-screen",
    "metrics": "financial-metrics",
    "coverage": "financial-coverage",
    "jobs": "financial-jobs",
}
ALLOWED = {
    "companies": {"search", "limit", "cursor", "expected_release_id"},
    "history": {
        "cik",
        "metrics",
        "frequency",
        "date_from",
        "date_to",
        "as_of",
        "limit",
        "cursor",
        "expected_release_id",
    },
    "batch": {
        "ciks",
        "metrics",
        "frequency",
        "date_from",
        "date_to",
        "as_of",
        "limit",
        "expected_release_id",
    },
    "screen": {
        "ciks",
        "minimum",
        "maximum",
        "sort",
        "limit",
        "cursor",
        "as_of",
        "expected_release_id",
        "metrics",
    },
    "metrics": {"cik", "expected_release_id"},
    "coverage": {"cik", "expected_release_id"},
    "jobs": {"job_id"},
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise HTTPError(request.full_url, code, "REDIRECT_DENIED", {}, None)


def open_request(request, timeout):
    return build_opener(NoRedirect()).open(request, timeout=timeout)


def local_credentials(path):
    """Minimal dotenv syntax, no interpolation, shell execution, or log output."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def fetch(base, token, route, params):
    request = Request(
        base + "/private/v1/" + route + ("?" + urlencode(params) if params else ""),
        headers={"Authorization": "Bearer " + token, "User-Agent": "Zion-Private-Research-CLI/1.0"},
    )
    try:
        with open_request(request, timeout=40) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        raise click.ClickException("PRIVATE_API_HTTP_" + str(error.code)) from None
    except (URLError, TimeoutError, OSError, ValueError, UnicodeError):
        raise click.ClickException("PRIVATE_API_TRANSPORT_FAILURE") from None
    if len(raw) > MAX_RESPONSE:
        raise click.ClickException("PRIVATE_API_RESPONSE_BUDGET_EXCEEDED")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise click.ClickException("PRIVATE_API_INVALID_JSON") from None
    return value, len(raw)


@click.command()
@click.argument("action", type=click.Choice(sorted(ROUTES)))
@click.option("--base-url", help="Explicit HTTPS API origin; redirects are denied.")
@click.option(
    "--credential-file",
    type=click.Path(path_type=Path),
    default=Path.home() / ".config/zion/private-api.env",
    show_default=True,
)
@click.option("--cik")
@click.option("--ciks")
@click.option("--metrics", help="Comma-separated history metrics; one metric for screen.")
@click.option("--frequency", type=click.Choice(["annual", "quarterly", "ttm"]))
@click.option("--date-from")
@click.option("--date-to")
@click.option("--as-of")
@click.option("--minimum")
@click.option("--maximum")
@click.option("--sort", type=click.Choice(["asc", "desc"]))
@click.option("--search")
@click.option("--limit", type=click.IntRange(1, 1000))
@click.option("--cursor")
@click.option("--expected-release-id")
@click.option("--job-id")
@click.option("--all-pages", is_flag=True, help="Collect at most 100 pages / 20 MiB, or fail.")
def research(action, base_url, credential_file, all_pages, **options):
    """Query private financial data without starting providers or the LLM orchestrator."""
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
    if not token or any(c in token for c in "\r\n"):
        raise click.ClickException("PRIVATE_API_CREDENTIAL_REQUIRED")
    supplied = {key: value for key, value in options.items() if value is not None}
    if set(supplied) - ALLOWED[action]:
        raise click.ClickException("OPTION_NOT_SUPPORTED_FOR_ACTION")
    if action in ("history", "coverage") and not supplied.get("cik"):
        raise click.ClickException("CIK_REQUIRED")
    if action == "batch" and not supplied.get("ciks"):
        raise click.ClickException("CIKS_REQUIRED")
    if action == "jobs" and not supplied.get("job_id"):
        raise click.ClickException("JOB_ID_REQUIRED")
    if action == "screen" and "metrics" in supplied:
        if "," in supplied["metrics"]:
            raise click.ClickException("SCREEN_REQUIRES_ONE_METRIC")
        supplied["metric"] = supplied.pop("metrics")
    if "search" in supplied:
        supplied["q"] = supplied.pop("search")
    if all_pages and action not in ("history", "companies", "screen"):
        raise click.ClickException("ALL_PAGES_UNSUPPORTED_FOR_ACTION")
    pages, total, seen, release = [], 0, set(), None
    for _ in range(100):
        value, size = fetch(base, token, ROUTES[action], supplied)
        total += size
        if total > MAX_TOTAL:
            raise click.ClickException("ALL_PAGES_TOTAL_BUDGET_EXCEEDED_NO_PARTIAL_OUTPUT")
        current_release = value.get("release_id") or value.get("provenance", {}).get("release_id")
        if release is not None and current_release != release:
            raise click.ClickException("PAGINATION_RELEASE_CHANGED_NO_PARTIAL_OUTPUT")
        release = current_release
        pages.append(value)
        cursor = value.get("next_cursor") or value.get("pagination", {}).get("next_cursor")
        if not all_pages or not cursor:
            output = (
                value
                if not all_pages
                else {
                    "pages": pages,
                    "page_count": len(pages),
                    "response_bytes": total,
                    "complete": True,
                }
            )
            click.echo(json.dumps(output, ensure_ascii=False, indent=2))
            return
        if cursor in seen:
            raise click.ClickException("PAGINATION_CURSOR_REPEATED_NO_PARTIAL_OUTPUT")
        seen.add(cursor)
        supplied["cursor"] = cursor
        if release:
            supplied["expected_release_id"] = release
    raise click.ClickException("ALL_PAGES_PAGE_BUDGET_EXCEEDED_NO_PARTIAL_OUTPUT")


if __name__ == "__main__":
    research()
