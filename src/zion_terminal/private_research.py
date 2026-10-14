"""Private SEC research client; standalone execution makes no provider or LLM calls."""

import json
import os
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
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


ROUTES.update(compare="financial-history", provenance="financial-history")
ALLOWED["compare"] = (ALLOWED["history"] - {"cik", "cursor"}) | {"ciks"}
ALLOWED["provenance"] = ALLOWED["history"]


def compare_histories(histories):
    """Align only identical periods and units; never silently choose a vintage."""
    groups = {}
    for cik, history in histories.items():
        for row in history.get("observations", []):
            key = tuple(
                row.get(k) for k in ("metric", "frequency", "period_start", "period_end", "unit")
            )
            group = groups.setdefault(key, {})
            if cik in group:
                raise click.ClickException("AMBIGUOUS_COMPARISON_PERIOD")
            try:
                if "numerator" in row and "denominator" in row:
                    value = Fraction(int(row["numerator"]), int(row["denominator"]))
                else:
                    raw = row["value_decimal"]
                    if not isinstance(raw, str):
                        raise ValueError()
                    value = Fraction(Decimal(raw))
            except (
                KeyError,
                ValueError,
                TypeError,
                ZeroDivisionError,
                InvalidOperation,
                OverflowError,
            ):
                raise click.ClickException("EXACT_FINANCIAL_VALUE_REQUIRED") from None
            group[cik] = (row, value)
    derived_gaps = {cik: [] for cik in histories}
    for key, revenues in list(groups.items()):
        if key[0] != "revenue":
            continue
        for cik, (revenue, denominator) in revenues.items():
            candidates = [("operating_margin", ("operating_income", *key[1:]))]
            if key[2] and key[3]:
                prior_dates = tuple(str(int(v[:4]) - 1) + v[4:] for v in key[2:4])
                candidates.append(("revenue_growth", ("revenue", key[1], *prior_dates, key[4])))
            for metric, other_key in candidates:
                other = groups.get(other_key, {}).get(cik)
                reason = None
                if other is None:
                    reason = (
                        "MATCHING_OPERATING_INCOME_MISSING"
                        if metric == "operating_margin"
                        else "EXACT_PRIOR_YEAR_PERIOD_MISSING"
                    )
                elif (denominator if metric == "operating_margin" else other[1]) == 0:
                    reason = "ZERO_REVENUE_DENOMINATOR"
                elif metric == "revenue_growth" and other[0].get("concept") != revenue.get(
                    "concept"
                ):
                    reason = "REVENUE_CONCEPT_CHANGED"
                if reason:
                    derived_gaps[cik].append(
                        {
                            "metric": metric,
                            "period_start": key[2],
                            "period_end": key[3],
                            "reason": reason,
                        }
                    )
                    continue
                value = (
                    other[1] / denominator
                    if metric == "operating_margin"
                    else (denominator - other[1]) / other[1]
                )
                source_ids = list(
                    dict.fromkeys(
                        other[0].get("source_observation_ids", [])
                        + revenue.get("source_observation_ids", [])
                    )
                )
                derived = {
                    **revenue,
                    "metric": metric,
                    "unit": "ratio",
                    "source_observation_ids": source_ids,
                    "derivation": "OPERATING_INCOME_DIV_REVENUE"
                    if metric == "operating_margin"
                    else "EXACT_CALENDAR_YEAR_OVER_YEAR",
                }
                groups.setdefault((metric, *key[1:4], "ratio"), {})[cik] = (derived, value)
    comparisons = []
    first = next(iter(histories), None)

    def exact(value):
        return {"numerator": str(value.numerator), "denominator": str(value.denominator)}

    for key, group in sorted(groups.items(), key=lambda item: tuple(str(v) for v in item[0])):
        record = dict(zip(("metric", "frequency", "period_start", "period_end", "unit"), key))
        record["values"] = {
            cik: exact(group[cik][1]) if cik in group else None for cik in histories
        }
        record["source_observation_ids"] = {
            cik: entry[0].get("source_observation_ids", []) for cik, entry in group.items()
        }
        record["missing_ciks"] = [cik for cik in histories if cik not in group]
        record["difference_from_first"] = {
            cik: exact(value[1] - group[first][1]) if first in group else None
            for cik, value in group.items()
        }
        comparisons.append(record)
    return {
        "alignment": "EXACT_PERIOD_START_END_FREQUENCY_UNIT",
        "baseline_cik": first,
        "derived_gaps": derived_gaps,
        "comparisons": comparisons,
        "missingness": "ABSENT_VALUES_ARE_NULL_NOT_ZERO",
    }


def collect_history(base, token, params, release=None):
    pages, seen, total = [], set(), 0
    params = dict(params)
    for _ in range(100):
        if release:
            params["expected_release_id"] = release
        value, size = fetch(base, token, ROUTES["history"], params)
        total += size
        if total > MAX_TOTAL:
            raise click.ClickException("ALL_PAGES_TOTAL_BUDGET_EXCEEDED_NO_PARTIAL_OUTPUT")
        current = value.get("release_id") or value.get("provenance", {}).get("release_id")
        if not current or (release and current != release):
            raise click.ClickException("PAGINATION_RELEASE_CHANGED_NO_PARTIAL_OUTPUT")
        release = current
        pages.append(value)
        cursor = value.get("next_cursor") or value.get("pagination", {}).get("next_cursor")
        if not cursor:
            return {
                "pages": pages,
                "observations": [r for p in pages for r in p.get("observations", [])],
                "release_id": release,
                "response_bytes": total,
                "complete": True,
            }
        if cursor in seen:
            raise click.ClickException("PAGINATION_CURSOR_REPEATED_NO_PARTIAL_OUTPUT")
        seen.add(cursor)
        params["cursor"] = cursor
    raise click.ClickException("ALL_PAGES_PAGE_BUDGET_EXCEEDED_NO_PARTIAL_OUTPUT")


def sec_filing_index(cik, accession):
    """Validate identifiers for SEC's documented filing-index symbolic path."""
    if (
        not isinstance(cik, str)
        or not re.fullmatch(r"[0-9]{1,10}", cik)
        or int(cik) == 0
        or not isinstance(accession, str)
        or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession)
    ):
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}-index.html"


def render_table(value, action):
    """Readable exact values; the exported dataset remains the unchanged JSON."""
    contexts = list(value.get("pages", [value]))
    for history in value.get("histories", {}).values():
        contexts.extend(history.get("pages", [history]))
    for page in list(contexts):
        for company in page.get("companies", []):
            if isinstance(company.get("result"), dict):
                contexts.append(company["result"])
    releases, cutoffs, qualifications, gaps = set(), set(), set(), set()
    rows = []

    def exact(row):
        if row is None:
            return "MISSING"
        if "numerator" in row and "denominator" in row:
            return str(row["numerator"]) + "/" + str(row["denominator"])
        return row.get("value_decimal", "NOT_RETURNED")

    def period(row):
        return (
            str(row.get("period_start") or "NOT_RETURNED")
            + ".."
            + str(row.get("period_end") or "NOT_RETURNED")
        )

    def add(cik, item, row, amount, quality, link="-"):
        rows.append(
            [
                cik or "NOT_RETURNED",
                item,
                period(row),
                row.get("unit") or "NOT_RETURNED",
                amount,
                quality,
                link,
            ]
        )

    for page in contexts:
        release = page.get("release_id") or page.get("provenance", {}).get("release_id")
        if release:
            releases.add(str(release))
        if page.get("as_of"):
            cutoffs.add(str(page["as_of"]))
        for key in ("qualification", "coverage_qualification", "release_quality"):
            if page.get(key):
                qualifications.add(str(page[key]))
        cik = page.get("cik")
        for gap in page.get("coverage_gaps", []) + page.get("missing_companies", []):
            gaps.add(json.dumps([cik, gap], sort_keys=True))
            add(
                gap.get("cik", cik),
                gap.get("metric", "SCREEN_MISSING"),
                gap,
                "MISSING",
                gap.get("reason") or "REASON_NOT_RETURNED",
            )
        for metric, definitions in (page.get("coverage") or {}).items():
            for name, counts in definitions.items():
                if not isinstance(counts, dict):
                    continue
                amount = counts.get("gaps", 0)
                for index in range(amount):
                    gaps.add(f"coverage:{cik}:{metric}:{name}:{index}")
                unit = page.get("metrics", {}).get(name, {}).get("unit")
                add(
                    cik,
                    metric + ":" + name,
                    {"unit": unit},
                    "observations=" + str(counts.get("observations", "NOT_RETURNED")),
                    "gaps=" + str(amount),
                )
        if action == "provenance":
            records = page.get("source_records", {})
            expected = {
                sid
                for row in page.get("observations", []) + page.get("accounting_checks", [])
                for sid in row.get("source_observation_ids", [])
            }
            for sid in sorted(set(records) | expected):
                leaf = records.get(sid)
                if leaf is None:
                    gaps.add("source_missing:" + sid)
                    add(cik, sid, {}, "MISSING", "SOURCE_RECORD_MISSING", "LINK_MISSING")
                    continue
                link = sec_filing_index(leaf.get("cik", cik), leaf.get("accession"))
                add(
                    leaf.get("cik", cik),
                    leaf.get("metric", sid),
                    leaf,
                    exact(leaf),
                    "SOURCE_RECORD_PRESENT" if link else "LINK_MISSING_INVALID_CIK_OR_ACCESSION",
                    link or "LINK_MISSING",
                )
        elif action != "compare":
            for row in page.get("observations", []):
                add(cik, row.get("metric", "NOT_RETURNED"), row, exact(row), "OBSERVED")
            for match in page.get("matches", []):
                row = match["observation"]
                add(
                    match["cik"],
                    match.get("metric", page.get("metric", "NOT_RETURNED")),
                    row,
                    exact(row),
                    "OBSERVED",
                )
            for company in page.get("companies", []):
                if company.get("http_status", 200) >= 400:
                    error = (
                        company.get("result", {}).get("error", {}).get("code", "ERROR_NOT_RETURNED")
                    )
                    gaps.add("batch_error:" + str(company.get("cik")))
                    add(
                        company.get("cik"),
                        "HTTP_" + str(company["http_status"]),
                        {},
                        "MISSING",
                        error,
                    )
                elif "result" not in company:
                    add(
                        company.get("cik"),
                        company.get("label") or "LABEL_NOT_RETURNED",
                        {},
                        "N/A",
                        company.get("quarantine_reason")
                        or company.get("status")
                        or str(company.get("http_status", "NOT_RETURNED")),
                    )
    for cik, derived in value.get("derived_gaps", {}).items():
        for gap in derived:
            gaps.add(json.dumps([cik, gap], sort_keys=True))
            add(
                cik,
                gap.get("metric", "DERIVED_GAP"),
                gap,
                "MISSING",
                gap.get("reason", "NOT_RETURNED"),
            )
    for row in value.get("comparisons", []):
        for cik, amount in row["values"].items():
            difference = row.get("difference_from_first", {}).get(cik)
            add(
                cik,
                row["metric"],
                row,
                exact(amount),
                "MISSING" if amount is None else "delta=" + exact(difference),
            )
    if not rows:
        rows.append(
            [
                value.get("cik", "N/A"),
                action,
                "NOT_RETURNED",
                "NOT_RETURNED",
                "NO_ROWS",
                value.get("status", "SEE_JSON"),
                "-",
            ]
        )

    def cell(item):
        return "".join(c if c.isprintable() else " " for c in str(item))

    rows = list(dict.fromkeys(tuple(cell(item) for item in row) for row in rows))
    headers = ["CIK", "ITEM", "REPORT_PERIOD", "UNIT", "EXACT_VALUE", "QUALITY", "SEC_FILING_INDEX"]
    widths = [
        max(len(header), max(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]

    def line(row):
        return " | ".join(text.ljust(width) for text, width in zip(row, widths)).rstrip()

    partial = any(
        page.get("next_cursor") or page.get("pagination", {}).get("next_cursor")
        for page in contexts
    )
    assembled_complete = value.get("complete") or (
        action == "compare"
        and value.get("histories")
        and all(history.get("complete") for history in value["histories"].values())
    )
    output = [
        "Release: " + (", ".join(sorted(releases)) or "NOT_RETURNED"),
        "As of: " + (", ".join(sorted(cutoffs)) or "NOT_RETURNED"),
        "Report period / unit: explicit per row; NOT_RETURNED means unavailable",
        "Quality gaps: " + str(len(gaps)),
        "Quality: " + ("; ".join(sorted(qualifications)) or "NOT_RETURNED"),
        "Pagination: "
        + (
            "MORE_PAGES_AVAILABLE"
            if partial and not assembled_complete
            else "NO_ADDITIONAL_CURSOR_IN_THIS_RESPONSE"
        ),
        line(headers),
        line(["-" * width for width in widths]),
    ]
    output.extend(line(row) for row in rows)
    if action == "provenance":
        output.append("SEC index links: identifier format validated; destination not fetched.")
    return "\n".join(output)


def emit_dataset(value, output, display_format="json", action="history"):
    serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        # Exclusive creation prevents accidental replacement of an existing research artifact.
        try:
            with output.open("x") as stream:
                stream.write(serialized)
        except OSError:
            raise click.ClickException("EXPORT_DESTINATION_UNWRITABLE_OR_EXISTS") from None
    click.echo(render_table(value, action) if display_format == "table" else serialized.rstrip())


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
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help="Export exact JSON including SEC lineage; destination must be new.",
)
@click.option(
    "--format",
    "display_format",
    type=click.Choice(["json", "table"]),
    default="json",
    show_default=True,
)
@click.option("--all-pages", is_flag=True, help="Collect at most 100 pages / 20 MiB, or fail.")
def research(action, base_url, credential_file, all_pages, output, display_format, **options):
    """Query private financial data without starting providers or the LLM orchestrator."""
    display_action = action
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
    if action == "compare":
        ciks = list(dict.fromkeys(supplied.pop("ciks", "").split(",")))
        if len(ciks) < 2 or len(ciks) > 20 or not all(ciks):
            raise click.ClickException("COMPARE_REQUIRES_2_TO_20_CIKS")
        if not supplied.get("frequency"):
            raise click.ClickException("COMPARE_FREQUENCY_REQUIRED")
        histories, release = {}, supplied.get("expected_release_id")
        history_params = dict(supplied)
        if "metrics" in history_params:
            metrics = history_params["metrics"].split(",")
            base_metrics = [m for m in metrics if m not in ("operating_margin", "revenue_growth")]
            if "revenue_growth" in metrics or "operating_margin" in metrics:
                base_metrics.append("revenue")
            if "operating_margin" in metrics:
                base_metrics.append("operating_income")
            history_params["metrics"] = ",".join(dict.fromkeys(base_metrics))
        for cik in ciks:
            histories[cik] = collect_history(base, token, {**history_params, "cik": cik}, release)
            if sum(h["response_bytes"] for h in histories.values()) > MAX_TOTAL:
                raise click.ClickException("ALL_PAGES_TOTAL_BUDGET_EXCEEDED_NO_PARTIAL_OUTPUT")
            release = histories[cik]["release_id"]
        cutoffs = {
            page["as_of"]
            for item in histories.values()
            for page in item["pages"]
            if page.get("as_of") is not None
        }
        if len(cutoffs) > 1:
            raise click.ClickException("COMPARE_REQUIRES_COMMON_AVAILABILITY_CUTOFF")
        result = compare_histories(histories)
        result.update(
            histories=histories,
            release_id=release,
            request={**supplied, "ciks": ciks},
            release_quality="SEE_RETAINED_API_PROVENANCE_AND_COVERAGE_GAPS",
            as_of=supplied.get("as_of") or histories[ciks[0]]["pages"][0].get("as_of"),
            base_url=base,
        )
        emit_dataset(result, output, display_format, display_action)
        return
    if action == "provenance":
        action = "history"
        all_pages = True
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
    export_path = output
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
            emit_dataset(output, export_path, display_format, display_action)
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
