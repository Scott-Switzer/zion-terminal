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


UNIFIED = {"overview", "prices", "valuation", "peers", "market-screen", "identity", "certification"}
ENTITY_OPTIONS = {
    "cik",
    "symbol",
    "security_id",
    "as_of",
    "expected_release_id",
    "expected_market_release_id",
}
for name in UNIFIED:
    ROUTES[name] = "research-" + ("screen" if name == "market-screen" else name)
ALLOWED.update(
    overview=ENTITY_OPTIONS | {"metrics", "date_to"},
    identity=ENTITY_OPTIONS | {"date_to"},
    prices=ENTITY_OPTIONS | {"date_from", "date_to", "limit", "cursor"},
    valuation=ENTITY_OPTIONS | {"metrics", "frequency", "date_from", "date_to"},
    peers={
        "ciks",
        "metrics",
        "frequency",
        "as_of",
        "expected_release_id",
        "expected_market_release_id",
    },
    certification={
        "cik",
        "metrics",
        "frequency",
        "as_of",
        "date_from",
        "date_to",
        "limit",
        "cursor",
        "expected_release_id",
    },
)
ALLOWED["market-screen"] = {
    "ciks",
    "fundamental_metric",
    "fundamental_minimum",
    "fundamental_maximum",
    "market_metric",
    "market_minimum",
    "market_maximum",
    "as_of",
    "limit",
    "cursor",
    "sort",
    "expected_release_id",
    "expected_market_release_id",
}


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
    market_releases = set()
    rows = []

    def exact(row):
        if row is None:
            return "MISSING"
        if row.get("numerator") is not None and row.get("denominator") is not None:
            return str(row["numerator"]) + "/" + str(row["denominator"])
        value = row.get("value_decimal", "NOT_RETURNED")
        return "MISSING" if value is None else value

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
        if "market_release_id" in page:
            market_releases.add(str(page["market_release_id"] or "NOT_AVAILABLE"))
        quality = page.get("quality")
        if quality:
            qualifications.add(
                json.dumps(quality, sort_keys=True) if isinstance(quality, dict) else str(quality)
            )
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
                quality = row.get("quality", row.get("status", "OBSERVED"))
                if isinstance(quality, dict):
                    quality = json.dumps(quality, sort_keys=True)
                if row.get("rule_ids"):
                    quality = (
                        str(quality)
                        + "; version="
                        + str(row.get("version", "NOT_RETURNED"))
                        + "; rules="
                        + ",".join(row["rule_ids"])
                    )
                if row.get("formula_id"):
                    quality = (
                        str(quality)
                        + "; formula="
                        + str(row["formula_id"])
                        + "@"
                        + str(row.get("formula_version", "NOT_RETURNED"))
                    )
                add(
                    row.get("cik", cik), row.get("metric", "NOT_RETURNED"), row, exact(row), quality
                )
            for row in page.get("prices", []):
                quality = row.get("quality", "SOURCE_QUALIFICATION_NOT_RETURNED")
                if isinstance(quality, dict):
                    quality = json.dumps(quality, sort_keys=True)
                if row.get("available_at") is None:
                    quality = str(quality) + "; HISTORICAL_PIT_UNAVAILABLE"
                add(
                    row.get("cik", cik),
                    "price:" + str(row.get("security_id", "NOT_RETURNED")),
                    {**row, "period_end": row.get("session_date")},
                    exact(row),
                    quality,
                )
            identity = page.get("identity")
            if isinstance(identity, dict):
                add(
                    identity.get("cik", cik),
                    "identity:" + str(identity.get("security_id", "NOT_RETURNED")),
                    {
                        "period_start": identity.get("valid_from"),
                        "period_end": identity.get("valid_to"),
                        "unit": "N/A",
                    },
                    identity.get("symbol", "SYMBOL_NOT_RETURNED"),
                    identity.get("status", "SEE_PROVENANCE"),
                )
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
        "Market release: " + (", ".join(sorted(market_releases)) or "NOT_RETURNED"),
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


def render_price_chart(value):
    """Text chart of returned prices only; exact labels remain unrounded."""
    pages = value.get("pages", [value])
    prices = [row for page in pages for row in page.get("prices", [])]
    chart = [
        "AUTHORIZED RETURNED PRICES; VISUAL HEIGHTS ARE NOT NUMERICAL OUTPUT",
        "SEC release: " + str(pages[0].get("release_id", "NOT_RETURNED")),
        "Market release: " + str(pages[0].get("market_release_id") or "NOT_AVAILABLE"),
    ]
    chart.append("As of financial availability: " + str(pages[0].get("as_of", "NOT_RETURNED")))
    chart.append("Quality gaps: " + str(sum(len(page.get("coverage_gaps", [])) for page in pages)))
    if prices:
        chart.append(
            "Price quality: "
            + "; ".join(
                sorted(
                    {
                        json.dumps(row.get("quality", "NOT_RETURNED"), sort_keys=True)
                        for row in prices
                    }
                )
            )
        )
        chart.append(
            "Adjustment policy: "
            + ", ".join(
                sorted({str(row.get("adjustment_policy", "NOT_RETURNED")) for row in prices})
            )
        )
    if not prices:
        chart.append("NO_AUTHORIZED_PRICE_OBSERVATIONS")
        chart.extend(
            str(gap.get("reason", "REASON_NOT_RETURNED"))
            for page in pages
            for gap in page.get("coverage_gaps", [])
        )
        return "\n".join(chart)
    scopes = {
        (r.get("security_id"), r.get("unit"), r.get("currency"), r.get("adjustment_policy"))
        for r in prices
    }
    if len(scopes) != 1:
        raise click.ClickException("CHART_REQUIRES_ONE_SECURITY_UNIT_AND_ADJUSTMENT_SCOPE")
    prices.sort(key=lambda row: row.get("session_date", ""))
    if len({r.get("session_date") for r in prices}) != len(prices):
        raise click.ClickException("CHART_AMBIGUOUS_PRICE_SESSION")
    numbers = []
    try:
        for row in prices:
            raw = row["value_decimal"]
            if not isinstance(raw, str) or len(raw) > 256:
                raise ValueError()
            number = Decimal(raw)
            if (
                not number.is_finite()
                or abs(number.adjusted()) > 80
                or abs(number.as_tuple().exponent) > 80
            ):
                raise ValueError()
            numbers.append(Fraction(number))
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        raise click.ClickException("CHART_EXACT_PRICE_VALUE_REQUIRED") from None
    if len(prices) == 1:
        chart.append("SINGLE_OBSERVATION_NO_TREND")
    else:
        low, high = min(numbers), max(numbers)
        bars = "▁▂▃▄▅▆▇█"
        # Bounded visual only; all original returned labels follow below.
        displayed = numbers[-120:]
        chart.append(
            "".join(
                bars[0 if high == low else int((n - low) * 7 / (high - low))] for n in displayed
            )
        )
        chart.append(
            f"Displayed last {len(displayed)} of {len(prices)} returned observations; "
            "no interpolated sessions."
        )
    chart.extend(
        str(r.get("session_date", "NOT_RETURNED"))
        + "  "
        + r["value_decimal"]
        + "  "
        + str(r.get("unit", "NOT_RETURNED"))
        for r in prices
    )
    if any(r.get("available_at") is None for r in prices):
        chart.append("HISTORICAL_PIT_UNAVAILABLE_FOR_ROWS_WITHOUT_AVAILABILITY")
    return "\n".join(chart)


def render_valuation_chart(value):
    """Plot exact returned formula fractions; unavailable periods remain gaps."""
    pages = value.get("pages", [value])
    rows = [r for page in pages for r in page.get("observations", [])]
    chart = [
        "RETURNED FORMULA SERIES; VISUAL HEIGHTS ARE NOT NUMERICAL OUTPUT",
        "SEC release: " + str(pages[0].get("release_id", "NOT_RETURNED")),
        "Research release: " + str(pages[0].get("research_release_id", "NOT_RETURNED")),
        "As of: " + str(pages[0].get("as_of", "NOT_RETURNED")),
        "Quality gaps: " + str(sum(len(p.get("coverage_gaps", [])) for p in pages)),
    ]
    scopes = {
        (r.get("metric"), r.get("cik"), r.get("unit"), r.get("currency"), r.get("frequency"))
        for r in rows
    }
    if len(scopes) > 1:
        raise click.ClickException("CHART_REQUIRES_ONE_METRIC_ISSUER_UNIT_CURRENCY_AND_FREQUENCY")
    rows = sorted(rows, key=lambda r: r.get("period_end", ""))
    if len({r.get("period_end") for r in rows}) != len(rows):
        raise click.ClickException("CHART_AMBIGUOUS_REPORT_PERIOD")
    numbers = []
    labels = []
    for r in rows:
        numerator = r.get("numerator", r.get("value_numerator"))
        denominator = r.get("denominator", r.get("value_denominator"))
        try:
            if numerator is not None and denominator is not None:
                if not re.fullmatch(r"-?\d{1,160}", str(numerator)) or not re.fullmatch(
                    r"\d{1,160}", str(denominator)
                ):
                    raise ValueError()
                number = Fraction(int(numerator), int(denominator))
                label = str(numerator) + "/" + str(denominator)
            elif r.get("value_decimal") is not None:
                raw = r["value_decimal"]
                if not isinstance(raw, str) or not re.fullmatch(r"-?\d{1,80}(?:\.\d{1,40})?", raw):
                    raise ValueError()
                number = Fraction(raw)
                label = raw
            else:
                number = None
                label = "MISSING"
        except (ValueError, ZeroDivisionError, TypeError):
            raise click.ClickException("CHART_EXACT_FORMULA_VALUE_REQUIRED") from None
        numbers.append(number)
        labels.append(label)
    valid = [n for n in numbers if n is not None]
    if len(valid) == 1:
        chart.append("SINGLE_OBSERVATION_NO_TREND")
    if valid:
        low, high = min(valid), max(valid)
        bars = "▁▂▃▄▅▆▇█"
        chart.append(
            "".join(
                "·" if n is None else bars[0 if high == low else int((n - low) * 7 / (high - low))]
                for n in numbers[-120:]
            )
        )
    else:
        chart.append("NO_AVAILABLE_FORMULA_OBSERVATIONS")
    chart.append("No interpolated periods; · denotes missing returned observation.")
    for r, label in zip(rows, labels):
        chart.append(
            str(r.get("period_end", "NOT_RETURNED"))
            + "  "
            + label
            + "  "
            + str(r.get("unit", "NOT_RETURNED"))
            + "  "
            + str(r.get("formula_id", "NOT_RETURNED"))
            + "/"
            + str(r.get("formula_version", "NOT_RETURNED"))
            + "  "
            + str(r.get("quality", "NOT_RETURNED"))
        )
    chart.extend(
        str(g.get("period_end", "NOT_RETURNED")) + "  GAP " + str(g.get("reason", "NOT_RETURNED"))
        for p in pages
        for g in p.get("coverage_gaps", [])
    )
    return "\n".join(chart)


def emit_dataset(value, output, display_format="json", action="history"):
    serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        # Exclusive creation prevents accidental replacement of an existing research artifact.
        try:
            with output.open("x") as stream:
                stream.write(serialized)
        except OSError:
            raise click.ClickException("EXPORT_DESTINATION_UNWRITABLE_OR_EXISTS") from None
    if display_format == "chart":
        rendered = (
            render_valuation_chart(value) if action == "valuation" else render_price_chart(value)
        )
    else:
        rendered = render_table(value, action) if display_format == "table" else serialized.rstrip()
    click.echo(rendered)


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
@click.option("--symbol")
@click.option("--security-id")
@click.option("--expected-market-release-id")
@click.option("--fundamental-metric")
@click.option("--fundamental-minimum")
@click.option("--fundamental-maximum")
@click.option("--market-metric")
@click.option("--market-minimum")
@click.option("--market-maximum")
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
    type=click.Choice(["json", "table", "chart"]),
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
    if display_format == "chart" and action not in ("prices", "valuation"):
        raise click.ClickException("CHART_REQUIRES_PRICES_OR_VALUATION_ACTION")
    if (
        display_format == "chart"
        and action == "valuation"
        and (not supplied.get("metrics") or len(supplied["metrics"].split(",")) != 1)
    ):
        raise click.ClickException("CHART_REQUIRES_ONE_METRIC")
    if action in ("overview", "prices", "valuation", "identity"):
        if sum(bool(supplied.get(key)) for key in ("cik", "symbol", "security_id")) != 1:
            raise click.ClickException("ONE_ENTITY_IDENTIFIER_REQUIRED")
    if action == "certification" and not supplied.get("cik"):
        raise click.ClickException("CIK_REQUIRED")
    if action == "peers":
        ciks = supplied.get("ciks", "").split(",")
        if (
            not 2 <= len(ciks) <= 20
            or len(ciks) != len(set(ciks))
            or any(not re.fullmatch(r"[0-9]{10}", cik) for cik in ciks)
        ):
            raise click.ClickException("PEERS_REQUIRES_2_TO_20_CIKS")
    if action == "market-screen":
        for prefix in ("fundamental", "market"):
            if any(
                supplied.get(prefix + suffix) is not None for suffix in ("_minimum", "_maximum")
            ) and not supplied.get(prefix + "_metric"):
                raise click.ClickException("SCREEN_FILTER_METRIC_REQUIRED")
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
    if all_pages and action not in (
        "history",
        "companies",
        "screen",
        "prices",
        "market-screen",
        "certification",
    ):
        raise click.ClickException("ALL_PAGES_UNSUPPORTED_FOR_ACTION")
    export_path = output
    pages, total, seen, release = [], 0, set(), None
    market_release = None
    for _ in range(100):
        value, size = fetch(base, token, ROUTES[action], supplied)
        total += size
        if total > MAX_TOTAL:
            raise click.ClickException("ALL_PAGES_TOTAL_BUDGET_EXCEEDED_NO_PARTIAL_OUTPUT")
        current_release = value.get("release_id") or value.get("provenance", {}).get("release_id")
        if release is not None and current_release != release:
            raise click.ClickException("PAGINATION_RELEASE_CHANGED_NO_PARTIAL_OUTPUT")
        release = current_release
        if action in UNIFIED:
            current_market = value.get("market_release_id")
            if (pages and current_market != market_release) or (
                supplied.get("expected_market_release_id") is not None
                and current_market != supplied["expected_market_release_id"]
            ):
                raise click.ClickException("PAGINATION_MARKET_RELEASE_CHANGED_NO_PARTIAL_OUTPUT")
            market_release = current_market
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
        if action in UNIFIED and market_release is not None:
            supplied["expected_market_release_id"] = market_release
    raise click.ClickException("ALL_PAGES_PAGE_BUDGET_EXCEEDED_NO_PARTIAL_OUTPUT")


if __name__ == "__main__":
    research()
