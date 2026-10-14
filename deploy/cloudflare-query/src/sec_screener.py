"""Bounded private screening over a hash-pinned, fixed-cutoff public SEC index."""

import asyncio
import json
import re
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from private_financials import PrivateFinancialsError
from private_http import authenticate
from sec_engine import REGISTRY, encoded
from sec_engine_api import artifact, cursor_decode, cursor_encode, fail, parameters, root, sha

VERSION = "sec-screen-index-v1"
METRICS = set(REGISTRY) | {"revenue_growth"}
MAX_INDEX = 8 * 1048576


def company_row(cik, data, observations):
    metrics, missing = {}, {}
    quarantined = data.get("policy", {}).get("quarantined_metrics", {})
    for metric in sorted(REGISTRY):
        rows = sorted(
            (r for r in observations if r["metric"] == metric), key=lambda r: r["period_end"]
        )
        if metric in quarantined:
            missing[metric] = quarantined[metric]
            continue
        if not rows:
            missing[metric] = "NO_VALID_ANNUAL_OBSERVATION"
            continue
        latest = rows[-1]
        metrics[metric] = {
            k: latest.get(k)
            for k in (
                "value_decimal",
                "period_start",
                "period_end",
                "available_at",
                "unit",
                "currency",
                "concept",
                "source_observation_ids",
                "source_terms",
            )
        }
        if metric == "revenue" and len(rows) >= 2:
            prior = rows[-2]
            contiguous = (
                date.fromisoformat(latest["period_start"]) - date.fromisoformat(prior["period_end"])
            ).days == 1
            if (
                contiguous
                and prior.get("concept") == latest.get("concept")
                and prior.get("unit") == latest.get("unit")
                and prior.get("currency") == latest.get("currency")
            ):
                base = Fraction(Decimal(prior["value_decimal"]))
                if base > 0:
                    growth = (Fraction(Decimal(latest["value_decimal"])) - base) / base
                    metrics["revenue_growth"] = {
                        "numerator": str(growth.numerator),
                        "denominator": str(growth.denominator),
                        "unit": "RATIO",
                        "period_end": latest["period_end"],
                        "prior_period_end": prior["period_end"],
                        "available_at": max(prior["available_at"], latest["available_at"]),
                        "source_observation_ids": sorted(
                            set(prior["source_observation_ids"] + latest["source_observation_ids"])
                        ),
                        "components": [prior, latest],
                        "definition": (
                            "(LATEST_ANNUAL_REVENUE-PRIOR_ANNUAL_REVENUE)/PRIOR_ANNUAL_REVENUE"
                        ),
                    }
    if "revenue_growth" not in metrics:
        missing["revenue_growth"] = (
            "REQUIRES_CONTIGUOUS_COMPARABLE_ANNUAL_REVENUE_AND_POSITIVE_BASE"
        )
    return {
        "cik": cik,
        "label": data.get("label"),
        "statement_family": data.get("policy", {}).get("family"),
        "member_sha256": data.get("member_sha256"),
        "metrics": metrics,
        "missing": missing,
    }


def build_index(manifest, basepath):
    """Build once from verified full annual materializations, never during a request."""
    base = Path(basepath)
    rows = []
    for cik in sorted(manifest["companies"]):
        data = json.loads((base / (cik + ".history.json")).read_text())
        annual = json.loads((base / (cik + ".annual.expected.json")).read_text())
        if (
            data["release_id"] != manifest["release_id"]
            or data["rights"] != "PUBLIC_SEC_ONLY"
            or annual["as_of"] != manifest["maximum_as_of"]
            or data["registry_sha256"] != manifest["registry_sha256"]
        ):
            raise ValueError("SCREEN_SOURCE_CONTEXT_MISMATCH")
        rows.append(company_row(cik, data, annual["observations"]))
    result = {
        "schema_version": VERSION,
        "release_id": manifest["release_id"],
        "registry_sha256": manifest["registry_sha256"],
        "as_of": manifest["maximum_as_of"],
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "frequency": "annual",
        "companies": rows,
        "qualification": (
            "LATEST_VALID_ANNUAL_PER_METRIC;FISCAL_ENDS_MAY_DIFFER;NOT_FULL_CERTIFICATION"
        ),
    }
    if len(encoded(result)) > MAX_INDEX:
        raise ValueError("SCREEN_INDEX_BUDGET")
    return result


def exact(value):
    number = Decimal(value)
    if (
        not number.is_finite()
        or len(value) > 128
        or abs(number.adjusted()) > 80
        or abs(number.as_tuple().exponent) > 80
    ):
        raise ValueError("INVALID_THRESHOLD")
    return Fraction(number)


def select(index, metric, minimum, maximum, direction, ciks):
    lower = exact(minimum) if minimum is not None else None
    upper = exact(maximum) if maximum is not None else None
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("INVALID_THRESHOLD_RANGE")
    found, missing = [], []
    for company in index["companies"]:
        if ciks and company["cik"] not in ciks:
            continue
        value = company["metrics"].get(metric)
        if value is None:
            missing.append({"cik": company["cik"], "reason": company["missing"].get(metric)})
            continue
        numeric = (
            Fraction(int(value["numerator"]), int(value["denominator"]))
            if metric == "revenue_growth"
            else exact(value["value_decimal"])
        )
        if (lower is None or numeric >= lower) and (upper is None or numeric <= upper):
            found.append(
                (
                    numeric,
                    {
                        "cik": company["cik"],
                        "label": company.get("label"),
                        "statement_family": company.get("statement_family"),
                        "member_sha256": company.get("member_sha256"),
                        "metric": metric,
                        "observation": value,
                    },
                )
            )
    found.sort(key=lambda item: item[1]["cik"])
    found.sort(key=lambda item: item[0], reverse=direction == "desc")
    return [item[1] for item in found], missing


async def execute(env, request):
    authenticate(env, request)
    if request.method != "GET":
        fail("READ_ONLY", 405)
    q = parameters(
        request.url,
        {
            "metric",
            "minimum",
            "maximum",
            "sort",
            "ciks",
            "limit",
            "cursor",
            "as_of",
            "expected_release_id",
            "recipient",
        },
    )
    if q.get("recipient", "human_research") != "human_research":
        fail("RECIPIENT_NOT_PERMITTED", 403)
    metric = q.get("metric", "revenue_growth")
    direction = q.get("sort", "desc")
    limit = int(q.get("limit", "25"))
    if metric not in METRICS or direction not in ("asc", "desc") or not 1 <= limit <= 100:
        fail("INVALID_SCREEN_PARAMETER", 422)
    m = await root(env)
    if q.get("expected_release_id", m["release_id"]) != m["release_id"]:
        fail("SOURCE_VERSION_CHANGED", 409)
    if q.get("as_of", m["maximum_as_of"]) != m["maximum_as_of"]:
        fail("SCREEN_SUPPORTS_FIXED_SNAPSHOT_CUTOFF_ONLY", 422)
    if not m.get("screening_index"):
        fail("SCREEN_INDEX_NOT_PUBLISHED", 503)
    index = await artifact(env, m["screening_index"], MAX_INDEX)
    if (
        index.get("schema_version") != VERSION
        or index.get("release_id") != m["release_id"]
        or index.get("registry_sha256") != m["registry_sha256"]
        or index.get("as_of") != m["maximum_as_of"]
        or index.get("rights") != "PUBLIC_SEC_ONLY"
        or index.get("licensed_sources_allowed") is not False
    ):
        fail("SCREEN_SOURCE_POLICY_OR_VERSION_INVALID", 403)
    ciks = q["ciks"].split(",") if q.get("ciks") else None
    if ciks and (
        len(ciks) > 100
        or len(ciks) != len(set(ciks))
        or any(not re.fullmatch(r"\d{10}", cik) for cik in ciks)
    ):
        fail("INVALID_SCREEN_CIKS", 422)
    supported = {c["cik"] for c in index["companies"]}
    if ciks and set(ciks) - supported:
        fail("SCREEN_CIK_NOT_MATERIALIZED", 404)
    found, missing = select(index, metric, q.get("minimum"), q.get("maximum"), direction, ciks)
    fingerprint = sha(encoded({k: v for k, v in q.items() if k not in ("cursor", "limit")}))
    offset = 0
    if q.get("cursor"):
        cursor = cursor_decode(q["cursor"], env.ZION_PRIVATE_API_TOKEN)
        if cursor.get("query") != fingerprint or cursor.get("release") != m["release_id"]:
            fail("CURSOR_QUERY_OR_RELEASE_MISMATCH", 409)
        offset = cursor.get("offset")
        if not isinstance(offset, int) or not 0 <= offset <= len(found):
            fail("INVALID_CURSOR_OFFSET", 422)
    end = min(len(found), offset + limit)
    result = {
        "release_id": m["release_id"],
        "as_of": index["as_of"],
        "frequency": "annual",
        "metric": metric,
        "matches": found[offset:end],
        "total_matches": len(found),
        "universe_companies": len(ciks) if ciks else len(supported),
        "missing_companies": missing,
        "missingness_policy": "EXCLUDED_WITH_REASON;NEVER_ZERO_IMPUTED",
        "next_cursor": cursor_encode(
            {"query": fingerprint, "release": m["release_id"], "offset": end},
            env.ZION_PRIVATE_API_TOKEN,
        )
        if end < len(found)
        else None,
        "provenance": {
            "screening_index_sha256": m["screening_index"]["sha256"],
            "registry_sha256": m["registry_sha256"],
            "licensed_sources_used": False,
        },
        "qualification": index["qualification"],
        "limits": {
            "max_page_matches": 100,
            "index_bytes": MAX_INDEX,
            "response_bytes": 2 * 1048576,
            "archive_bytes_read": 0,
        },
    }
    if len(encoded(result)) > 2 * 1048576:
        fail("SCREEN_RESPONSE_BUDGET_EXCEEDED", 413)
    return result, 200


async def handle(env, request):
    try:
        return await asyncio.wait_for(execute(env, request), timeout=20)
    except PrivateFinancialsError as error:
        return {"error": {"code": error.code}}, error.status
    except (ValueError, TypeError):
        return {"error": {"code": "INVALID_SCREEN_PARAMETER_OR_CURSOR"}}, 422
    except TimeoutError:
        return {"error": {"code": "SCREEN_QUERY_TIMEOUT"}}, 504
    except Exception:
        return {"error": {"code": "SCREEN_UNAVAILABLE"}}, 503
