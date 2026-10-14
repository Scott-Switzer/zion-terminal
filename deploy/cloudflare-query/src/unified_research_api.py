"""Read-only bounded research orchestration over immutable, hash-pinned sources."""

import asyncio
import hashlib
import json
import re
from datetime import date
from fractions import Fraction
from urllib.parse import urlparse

import sec_engine_api as sec
from financial_certification import certify_history
from private_financials import PrivateFinancialsError
from private_http import authenticate
from sec_engine import REGISTRY, HistoryError, history
from security_identity import resolve_point
from valuation_engine import FORMULAS, calculate

MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PREFIX = "gold/unified-research/releases/"
BASE = list(REGISTRY)
ENTITY = {
    "cik",
    "symbol",
    "security_id",
    "as_of",
    "expected_release_id",
    "expected_market_release_id",
    "date_to",
}
ALLOWED = {
    "overview": ENTITY | {"metrics"},
    "identity": ENTITY,
    "prices": ENTITY | {"date_from", "limit", "cursor"},
    "valuation": ENTITY | {"metrics", "frequency", "date_from"},
    "peers": {
        "ciks",
        "metrics",
        "frequency",
        "as_of",
        "expected_release_id",
        "expected_market_release_id",
    },
    "certification": {
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
    "screen": {
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
    },
}


def fail(code, status=400):
    sec.fail(code, status)


class MemoBody:
    """R2 bodies are one-shot; memoize consumed text, never reuse the stream."""

    def __init__(self, body):
        self.body = body
        self.lock = asyncio.Lock()
        self.value = None

    def __getattr__(self, key):
        return getattr(self.body, key)

    async def text(self):
        async with self.lock:
            if self.value is None:
                self.value = await self.body.text()
            return self.value


class BudgetEnv:
    def __init__(self, env):
        self.original = env
        self.used = 0
        self.reads = 0
        self.cache_hits = 0
        self.objects = {}
        self.semaphore = asyncio.Semaphore(4)
        self.MARKET_DATA = self

    def __getattr__(self, key):
        return getattr(self.original, key)

    async def get(self, key):
        if key in self.objects:
            self.cache_hits += 1
            return self.objects[key]
        async with self.semaphore:
            if key in self.objects:
                return self.objects[key]
            obj = await self.original.MARKET_DATA.get(key)
            if obj:
                self.reads += 1
                self.used += int(obj.size)
                if self.used > MAX_SOURCE_BYTES:
                    fail("SOURCE_READ_BUDGET", 413)
            wrapped = MemoBody(obj) if obj else None
            self.objects[key] = wrapped
            return wrapped


async def artifact(env, ref):
    key = ref.get("key", "")
    digest = ref.get("sha256", "")
    if (
        not key.startswith(PREFIX)
        or any(x in ("", ".", "..") for x in key.split("/"))
        or not re.fullmatch("[a-f0-9]{64}", digest)
    ):
        fail("SOURCE_REFERENCE_INVALID", 503)
    obj = await env.get(key)
    if not obj:
        fail("SOURCE_MISSING", 503)
    raw = (await obj.text()).encode()
    if len(raw) > MAX_SOURCE_BYTES:
        fail("SOURCE_READ_BUDGET", 413)
    if hashlib.sha256(raw).hexdigest() != digest or ("bytes" in ref and len(raw) != ref["bytes"]):
        fail("SOURCE_VERSION_CHANGED", 409)
    return json.loads(raw)


def sources(data, ids):
    result = {}
    for row in data["observations"]:
        sid = row["source_observation_id"]
        if sid in ids:
            item = dict(row)
            record = dict(item.get("source_record", {}))
            if "val" in record:
                record["val"] = str(record["val"])
            item["source_record"] = record
            result[sid] = item
    return result


def indexed_entry(root, manifest, cik):
    entries = root.get("financial_entries", {})
    if not isinstance(entries, dict) or len(entries) > 40:
        fail("FINANCIAL_INDEX_INVALID", 503)
    if cik not in entries:
        return None
    item = entries[cik]
    reference = item.get("materialized", {})
    certification = root.get("certifications", {}).get(cik, {})
    if (
        not root.get("financial_manifest_ref")
        or item.get("source_shard_ref") != manifest.get("shards", {}).get(cik[-2:])
        or item.get("quarantine")
        or not reference
        or reference.get("member_sha256") != certification.get("member_sha256")
        or certification.get("registry_sha256") != manifest.get("registry_sha256")
        or reference.get("source_release_id") not in manifest.get("source_releases", [])
    ):
        fail("FINANCIAL_INDEX_SOURCE_MISMATCH", 409)
    return {k: v for k, v in item.items() if k != "source_shard_ref"}


async def financial(env, root, m, cik, p):
    e = indexed_entry(root, m, cik)
    if e is None:
        e = await sec.entry(env, m, cik)
    data = await sec.company(env, m, cik, e)
    if e.get("materialized", {}).get("member_sha256") and (
        data.get("member_sha256") != e["materialized"]["member_sha256"]
    ):
        fail("FINANCIAL_INDEX_MEMBER_MISMATCH", 409)
    scope = dict(m)
    for key in ("maximum_as_of", "date_to"):
        if e.get(key):
            scope[key] = e[key]
    q = sec.validate(
        {
            "cik": cik,
            **{k: v for k, v in p.items() if k in ("frequency", "date_from", "date_to", "as_of")},
            "metrics": ",".join(BASE),
        },
        scope,
    )
    copied = dict(data)
    companion = None
    proof = None
    if cik in root.get("certifications", {}):
        companion = await artifact(env, root["certifications"][cik])
        if (
            companion.get("rights") != "PUBLIC_SEC_ONLY"
            or companion.get("member_sha256") != data.get("member_sha256")
            or companion.get("registry_sha256") != data.get("registry_sha256")
        ):
            fail("CERTIFICATION_SOURCE_MISMATCH", 409)
        copied["observations"] = data["observations"] + companion.get("observations", [])
        proof = companion.get("historical_reconciliation", {}).get(q["as_of"])
        if not proof and companion.get("as_of") == q["as_of"]:
            proof = companion.get("reconciliation_evidence")
    h = history(
        copied,
        metrics=q["metrics"],
        frequency=q["frequency"],
        date_from=q["date_from"],
        date_to=q["date_to"],
        as_of=q["as_of"],
    )
    cert = certify_history(
        copied,
        h,
        as_of=q["as_of"],
        reconciliation_evidence=proof,
        accounting_inputs=(companion or {}).get("accounting_inputs", []),
    )
    lookup = {r["source_observation_id"]: r for r in copied["observations"]}
    verified = {
        (r["metric"], r["period_start"], r["period_end"]): r
        for r in cert["observations"]
        if r["verified_statuses"]
    }
    for row in h["observations"]:
        key = (row["metric"], row.get("period_start"), row.get("period_end"))
        if key not in verified:
            continue
        leaves = [lookup[s] for s in row.get("source_observation_ids", []) if s in lookup]
        if (
            row["metric"] == "capital_expenditures"
            and leaves
            and all(
                r.get("concept") == "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
                for r in leaves
            )
            and Fraction(row["value_decimal"]) >= 0
        ):
            row["definition"] = "CASH_PPE_PURCHASES_POSITIVE_OUTFLOW"
    for row in h["observations"]:
        if row["metric"] != "free_cash_flow":
            continue
        same = {
            r["metric"]: r
            for r in h["observations"]
            if (r.get("period_start"), r.get("period_end"), r.get("unit"))
            == (row.get("period_start"), row.get("period_end"), row.get("unit"))
        }
        ocf = same.get("operating_cash_flow")
        capex = same.get("capital_expenditures")
        if (
            ocf
            and capex
            and capex.get("definition") == "CASH_PPE_PURCHASES_POSITIVE_OUTFLOW"
            and (row["metric"], row.get("period_start"), row.get("period_end")) in verified
        ):

            def terms(r):
                return {
                    t["source_observation_id"]: Fraction(str(t["coefficient"]))
                    for t in r.get("source_terms", [])
                }

            expected = terms(ocf)
            for sid, coefficient in terms(capex).items():
                expected[sid] = expected.get(sid, Fraction(0)) - coefficient
            expected = {k: v for k, v in expected.items() if v}
            if expected == terms(row) and Fraction(row["value_decimal"]) == Fraction(
                ocf["value_decimal"]
            ) - Fraction(capex["value_decimal"]):
                row["definition"] = "OPERATING_CASH_FLOW_MINUS_CASH_PPE_PURCHASES_VERIFIED"
    return copied, h, cert, q


def formula_rows(h, metrics, cutoff, cik):
    rows = h["observations"]
    out = []
    periods = sorted(
        {
            (r["period_start"], r["period_end"])
            for r in rows
            if r.get("period_start") and r.get("period_end")
        }
    )
    for metric in metrics:
        if metric not in FORMULAS:
            fail("INVALID_METRIC", 422)
        metric_out = []
        for start, end in periods or [(None, None)]:
            inputs = {
                r["metric"]: dict(r, source_ids=r.get("source_observation_ids", []))
                for r in rows
                if (r.get("period_start"), r.get("period_end")) == (start, end)
            }
            revenue = inputs.get("revenue")
            if revenue:
                prior = [
                    r
                    for r in rows
                    if r["metric"] == "revenue"
                    and 350
                    <= (date.fromisoformat(end) - date.fromisoformat(r["period_end"])).days
                    <= 378
                    and abs(
                        (date.fromisoformat(end) - date.fromisoformat(start)).days
                        - (
                            date.fromisoformat(r["period_end"])
                            - date.fromisoformat(r["period_start"])
                        ).days
                    )
                    <= 8
                ]
                if len(prior) == 1:
                    inputs["prior_revenue"] = dict(
                        prior[0], source_ids=prior[0].get("source_observation_ids", [])
                    )
            value = calculate(metric, inputs, as_of=cutoff)
            value.update(metric=metric, cik=cik, period_start=start, period_end=end)
            value["numerator"] = value.get("value_numerator")
            value["denominator"] = value.get("value_denominator")
            metric_out.append(value)
        valid = [r for r in metric_out if r.get("value_numerator") is not None]
        out.extend(valid or metric_out[-1:])
    return out


async def market_data(env, root, p, anchors):
    if "as_of" in p:
        fail("MARKET_POINT_IN_TIME_NOT_ESTABLISHED", 422)
    for field in ("date_from", "date_to"):
        if p.get(field):
            date.fromisoformat(p[field])
    if p.get("date_from", "0000") > p.get("date_to", "9999"):
        fail("INVALID_DATE_RANGE", 422)
    market = await artifact(env, root["market"])
    if market.get("rights") != "PRIVATE_OWNER_ALPACA_RESEARCH_ONLY":
        fail("SOURCE_POLICY_NOT_PERMITTED", 403)
    symbol = p.get("symbol")
    point = None
    if p.get("cik"):
        if not p.get("date_to"):
            fail("EXACT_IDENTITY_DATE_REQUIRED", 422)
        candidates = [
            a
            for a in anchors
            if a.get("cik") == p["cik"]
            and a.get("observed_on") == p["date_to"]
            and a.get("symbol") in market.get("symbols", {})
        ]
        symbols = {a["symbol"] for a in candidates}
        if len(symbols) != 1:
            fail("DATED_IDENTITY_NOT_ESTABLISHED", 422)
        symbol = next(iter(symbols))
        point = resolve_point(anchors, symbol, p["date_to"], as_of=None)
        if point["cik"] != p["cik"]:
            fail("DATED_IDENTITY_NOT_ESTABLISHED", 422)
    if not symbol and p.get("security_id"):
        symbol = p["security_id"].split(":")[-1]
    if not symbol or symbol not in market.get("symbols", {}):
        fail("MARKET_SOURCE_SYMBOL_UNAVAILABLE", 404)
    payload = await artifact(env, market["symbols"][symbol])
    if (
        payload.get("rights") != market["rights"]
        or payload.get("price_basis") != "RAW"
        or payload.get("adjustment_method") != "NONE"
        or payload.get("numeric_representation") != "ORIGINAL_RAW_DECIMAL_TOKEN_VERIFIED"
    ):
        fail("MARKET_DEFINITION_UNSUPPORTED", 422)
    if p.get("security_id") and p["security_id"] != payload.get("security_id"):
        fail("SECURITY_ID_MISMATCH", 422)
    prices = []
    for row in payload.get("observations", []):
        day = row["session_date"]
        if day < p.get("date_from", "0000") or day > p.get("date_to", "9999"):
            continue
        token = row.get("close")
        if not isinstance(token, str) or not re.fullmatch(r"-?\d{1,80}(?:\.\d{1,40})?", token):
            fail("MARKET_NUMERIC_ENCODING", 422)
        if (
            row.get("currency") != "USD"
            or row.get("currency_evidence", {}).get("currency") != "USD"
        ):
            fail("MARKET_CURRENCY_UNSUPPORTED", 422)
        ids = row.get("source_record_ids", [])
        if not ids or any(s not in payload.get("source_records", {}) for s in ids):
            fail("MARKET_PROVENANCE_INCOMPLETE", 422)
        prices.append(
            {
                "session_date": day,
                "value_decimal": token,
                "unit": "USD",
                "currency": "USD",
                "available_at": None,
                "security_id": payload["security_id"],
                "source_ids": ids,
                "quality": "RETROSPECTIVE_PIT_NOT_ESTABLISHED",
            }
        )
    return market, payload, prices, point


async def execute(original, request):
    authenticate(original, request)
    action = urlparse(request.url).path.removeprefix("/private/v1/research-")
    if request.method != "GET":
        fail("METHOD_NOT_ALLOWED", 405)
    if action not in ALLOWED:
        fail("NOT_FOUND", 404)
    p = sec.parameters(request.url, ALLOWED[action])
    if action not in ("peers", "screen"):
        identifiers = [k for k in ("cik", "symbol", "security_id") if p.get(k)]
        if len(identifiers) != 1:
            fail("EXACTLY_ONE_IDENTIFIER_REQUIRED", 422)
        if action in ("valuation", "certification") and identifiers != ["cik"]:
            fail("FINANCIAL_CIK_REQUIRED", 422)
    if action in ("overview", "certification") and p.get("metrics"):
        if any(x not in BASE for x in p["metrics"].split(",")):
            fail("INVALID_METRIC", 422)
    if action == "screen":
        if p.get("ciks") and (
            len(p["ciks"].split(",")) > 20
            or any(not re.fullmatch(r"\d{10}", x) for x in p["ciks"].split(","))
        ):
            fail("INVALID_SCREEN_CIKS", 422)
        for key in (
            "fundamental_minimum",
            "fundamental_maximum",
            "market_minimum",
            "market_maximum",
        ):
            if key in p and not re.fullmatch(r"-?\d{1,80}(?:\.\d{1,40})?", p[key]):
                fail("INVALID_SCREEN_THRESHOLD", 422)
    env = BudgetEnv(original)
    root = await artifact(
        env,
        {
            "key": getattr(env, "UNIFIED_RESEARCH_MANIFEST_KEY", ""),
            "sha256": getattr(env, "UNIFIED_RESEARCH_MANIFEST_SHA256", ""),
        },
    )
    policy = {
        "public_sec": True,
        "owner_market": "PRIVATE_OWNER_ALPACA_RESEARCH_ONLY",
        "human_research_only": True,
        "source_prices_not_llm": True,
        "licensed_wrds_allowed": False,
    }
    if root.get("schema_version") != "unified-research-release-v1" or any(
        root.get("source_policy", {}).get(k) != v for k, v in policy.items()
    ):
        fail("SOURCE_POLICY_NOT_PERMITTED", 403)
    financial_ref = root.get("financial_manifest_ref")
    if financial_ref and (
        financial_ref.get("key") != getattr(env, "SEC_ENGINE_MANIFEST_KEY", "")
        or financial_ref.get("sha256") != getattr(env, "SEC_ENGINE_MANIFEST_SHA256", "")
    ):
        fail("FINANCIAL_MANIFEST_PIN_MISMATCH", 409)
    m = await sec.root(env)
    if (
        root["financial_release_id"] != m["release_id"]
        or p.get("expected_release_id", m["release_id"]) != m["release_id"]
    ):
        fail("SOURCE_VERSION_CHANGED", 409)
    result = {
        "schema_version": "unified-research-v1",
        "research_release_id": root.get("research_release_id", root.get("release_id")),
        "release_id": m["release_id"],
        "market_release_id": None,
        "as_of": p.get("as_of", m["maximum_as_of"]),
        "observations": [],
        "coverage_gaps": [],
        "source_records": {},
        "quality": "EVIDENCE_SCOPED",
        "provenance": {},
        "limits": {
            "maximum_source_bytes": MAX_SOURCE_BYTES,
            "maximum_response_bytes": MAX_RESPONSE_BYTES,
        },
    }
    identity = await artifact(env, root["identity"])
    anchors = identity.get("identity_anchors", [])
    if identity.get("rights") != "PUBLIC_SEC_ONLY":
        fail("SOURCE_POLICY_NOT_PERMITTED", 403)
    if action in ("prices", "identity"):
        if action == "identity":
            selected = [
                a
                for a in anchors
                if (not p.get("cik") or a.get("cik") == p["cik"])
                and (not p.get("symbol") or a.get("symbol") == p["symbol"])
                and (not p.get("date_to") or a.get("observed_on") == p["date_to"])
            ]
            if p.get("security_id"):
                fail("DATED_SECURITY_IDENTITY_NOT_ESTABLISHED", 422)
            if p.get("as_of"):
                if not p.get("date_to"):
                    fail("EXACT_IDENTITY_DATE_REQUIRED", 422)
                selected = [
                    resolve_point(anchors, a["symbol"], p["date_to"], as_of=p["as_of"])
                    for a in selected
                ]
                if not selected:
                    fail("DATED_SECURITY_IDENTITY_NOT_ESTABLISHED", 422)
            result["observations"] = selected
            result["quality"] = "POINT_EVIDENCE_ONLY_NO_INTERVAL_INFERENCE"
        else:
            market, payload, prices, _ = await market_data(env, root, p, anchors)
            result.update(
                market_release_id=market["release_id"],
                as_of=None,
                prices=prices,
                source_records=payload.get("source_records", {}),
                provenance={"source_pages": payload.get("source_pages", {})},
                quality="RETROSPECTIVE_PIT_NOT_ESTABLISHED",
            )
    elif action == "screen":
        if "as_of" in p:
            fail("MARKET_POINT_IN_TIME_NOT_ESTABLISHED", 422)
        if p.get("market_metric", "close") != "close" or p.get(
            "fundamental_metric", "revenue_growth"
        ) not in ("revenue_growth", "operating_margin"):
            fail("UNSUPPORTED_SCREEN_METRIC", 422)
        approved = root.get("screen_cohort", [])
        if not p.get("ciks") and (not approved or len(approved) > 20):
            fail("EXPLICIT_SCREEN_CIKS_REQUIRED", 422)
        ciks = p["ciks"].split(",") if p.get("ciks") else approved
        if len(ciks) > 20:
            fail("PEER_LIMIT", 422)
        result["as_of"] = None
        matches = []
        covered_market = await artifact(env, root["market"])
        for cik in ciks:
            points = {
                (x["symbol"], x["observed_on"]): x
                for x in anchors
                if x.get("cik") == cik and x.get("symbol") in covered_market["symbols"]
            }
            for a in points.values():
                try:
                    market, payload, prices, _ = await market_data(
                        env,
                        root,
                        {"cik": cik, "date_to": a["observed_on"], "date_from": a["observed_on"]},
                        anchors,
                    )
                except PrivateFinancialsError as exc:
                    result["coverage_gaps"].append({"cik": cik, "reason": exc.code})
                    continue
                result["market_release_id"] = market["release_id"]
                if len(prices) != 1:
                    result["coverage_gaps"].append(
                        {
                            "cik": cik,
                            "symbol": a["symbol"],
                            "date": a["observed_on"],
                            "reason": "EXACT_PRICE_POINT_UNAVAILABLE",
                        }
                    )
                    continue
                cutoff = a["observed_on"] + "T00:00:00Z"
                data, h, cert, q = await financial(
                    env,
                    root,
                    m,
                    cik,
                    {"as_of": cutoff, "date_to": a["observed_on"], "date_from": "2014-01-01"},
                )
                verified = {
                    tuple(r.get(k) for k in ("metric", "period_start", "period_end"))
                    for r in cert["observations"]
                    if any(
                        s in r["verified_statuses"]
                        for s in ("INDEPENDENTLY_RECONCILED", "ACCOUNTING_CERTIFIED")
                    )
                }
                h = dict(
                    h,
                    observations=[
                        r
                        for r in h["observations"]
                        if tuple(r.get(k) for k in ("metric", "period_start", "period_end"))
                        in verified
                    ],
                )
                rows = formula_rows(h, [p.get("fundamental_metric", "revenue_growth")], cutoff, cik)
                rows = [r for r in rows if r.get("value_numerator") is not None]
                if not rows:
                    result["coverage_gaps"].append(
                        {
                            "cik": cik,
                            "symbol": a["symbol"],
                            "date": a["observed_on"],
                            "metric": p.get("fundamental_metric", "revenue_growth"),
                            "reason": "VERIFIED_FUNDAMENTAL_METRIC_UNAVAILABLE",
                        }
                    )
                    continue
                value = rows[-1]
                number = Fraction(int(value["value_numerator"]), int(value["value_denominator"]))
                price = Fraction(prices[0]["value_decimal"])
                good = True
                for prefix, n in [("fundamental", number), ("market", price)]:
                    for suffix, op in [
                        ("minimum", lambda x, y: x >= y),
                        ("maximum", lambda x, y: x <= y),
                    ]:
                        if prefix + "_" + suffix in p and not op(
                            n, Fraction(p[prefix + "_" + suffix])
                        ):
                            good = False
                if good:
                    matches.append(
                        {
                            "cik": cik,
                            "symbol": a["symbol"],
                            "financial_as_of": cutoff,
                            "price_date": a["observed_on"],
                            "identity_known_at": a.get("known_at"),
                            "fundamental": value,
                            "price": prices[0],
                        }
                    )
                result["source_records"].update(sources(data, set(value.get("source_ids", []))))
                result["source_records"].update(
                    {sid: payload["source_records"][sid] for sid in prices[0]["source_ids"]}
                )
                page_ids = {
                    payload["source_records"][sid].get("source_page_sha256")
                    for sid in prices[0]["source_ids"]
                }
                result["provenance"].setdefault("source_pages", {}).update(
                    {
                        sid: record
                        for sid, record in payload.get("source_pages", {}).items()
                        if sid in page_ids
                    }
                )
        result.update(
            matches=matches,
            total_matches=len(matches),
            qualification="RETROSPECTIVE_EXACT_FILING_POINTS;FINANCIAL_CUTOFF_IS_ANALYSIS_POLICY_NOT_PRICE_PUBLICATION_TIME",
        )
    else:
        if action == "overview" and not p.get("cik"):
            if not p.get("symbol") or not p.get("date_to"):
                fail("EXACT_IDENTITY_DATE_REQUIRED", 422)
            point = resolve_point(anchors, p["symbol"], p["date_to"], as_of=p.get("as_of"))
            p = {**p, "cik": point["cik"], "as_of": p.get("as_of", p["date_to"] + "T00:00:00Z")}
            result["identity"] = point
            result["identity_known_at"] = point.get("known_at")
            result["qualification"] = "EXACT_FILING_POINT;FINANCIAL_CUTOFF_IS_ANALYSIS_POLICY"
        ciks = p.get("ciks", p.get("cik", "")).split(",")
        if action == "peers" and not 2 <= len(set(ciks)) <= 20:
            fail("PEER_LIMIT", 422)
        if any(not re.fullmatch(r"\d{10}", c) for c in ciks):
            fail("INVALID_CIK", 422)
        for cik in ciks:
            try:
                data, h, cert, q = await financial(env, root, m, cik, p)
            except PrivateFinancialsError as exc:
                if action != "peers":
                    raise
                result["coverage_gaps"].append({"cik": cik, "reason": exc.code})
                continue
            result["as_of"] = q["as_of"]
            if action == "certification":
                rows = cert["observations"]
                if p.get("metrics"):
                    rows = [r for r in rows if r["metric"] in p["metrics"].split(",")]
                result["certification_version"] = cert["version"]
            elif action == "overview":
                rows = h["observations"]
                if p.get("metrics"):
                    rows = [r for r in rows if r["metric"] in p["metrics"].split(",")]
            else:
                rows = formula_rows(
                    h,
                    p.get(
                        "metrics", "revenue_growth,operating_margin,net_margin,free_cash_flow"
                    ).split(","),
                    q["as_of"],
                    cik,
                )
            if action in ("overview", "peers"):
                latest = {r["metric"]: r for r in rows}
                rows = list(latest.values())
            for row in rows:
                if action == "certification":
                    row["source_ids"] = [e["source_id"] for e in row.get("source_evidence", [])]
                    row["source_ids"] = sorted(
                        set(row["source_ids"])
                        | set(row.get("accounting_evidence", {}).get("component_ids", {}).values())
                    )
            result["observations"].extend(rows)
            result["coverage_gaps"].extend(h.get("coverage_gaps", []))
            ids = {
                s for r in rows for s in r.get("source_ids", r.get("source_observation_ids", []))
            }
            if action == "certification":
                ids.update(
                    s for r in h["observations"] for s in r.get("source_observation_ids", [])
                )
            result["source_records"].update(sources(data, ids))
    if (
        p.get("expected_market_release_id", result["market_release_id"])
        != result["market_release_id"]
    ):
        fail("MARKET_SOURCE_VERSION_CHANGED", 409)
    if action in ("prices", "certification", "screen"):
        limit = int(p.get("limit", "100"))
        if not 1 <= limit <= 1000:
            fail("INVALID_LIMIT", 422)
        key = {"prices": "prices", "certification": "observations", "screen": "matches"}[action]
        items = result[key]
        offset = 0
        query = hashlib.sha256(
            json.dumps(
                {k: v for k, v in p.items() if k not in ("cursor", "limit")}, sort_keys=True
            ).encode()
        ).hexdigest()
        binding = [result["research_release_id"], result["release_id"], result["market_release_id"]]
        if p.get("cursor"):
            c = sec.cursor_decode(p["cursor"], env.ZION_PRIVATE_API_TOKEN)
            if c.get("query") != query or c.get("releases") != binding:
                fail("CURSOR_QUERY_OR_RELEASE_MISMATCH", 409)
            offset = c.get("offset")
            if not isinstance(offset, int) or offset < 0 or offset > len(items):
                fail("INVALID_CURSOR", 422)
        result[key] = items[offset : offset + limit]
        end = offset + len(result[key])
        result["next_cursor"] = (
            sec.cursor_encode(
                {"query": query, "releases": binding, "offset": end}, env.ZION_PRIVATE_API_TOKEN
            )
            if end < len(items)
            else None
        )
        result["total_rows"] = len(items)
        result["coverage_complete_in_this_response"] = offset == 0 and end == len(items)
    if action in ("prices", "certification", "screen"):
        selected = (
            result.get("prices", []) if action == "prices" else result.get("observations", [])
        )
        ids = {
            sid
            for row in selected
            for sid in row.get("source_ids", row.get("source_observation_ids", []))
        }
        if action == "screen":
            ids = {
                sid
                for row in result["matches"]
                for component in (row["fundamental"], row["price"])
                for sid in component.get("source_ids", [])
            }
        result["source_records"] = {
            sid: record for sid, record in result["source_records"].items() if sid in ids
        }
        if action in ("prices", "screen"):
            pages = {r.get("source_page_sha256") for r in result["source_records"].values()}
            result["provenance"]["source_pages"] = {
                key: value
                for key, value in result["provenance"].get("source_pages", {}).items()
                if key in pages
            }
    result["limits"]["source_bytes_read"] = env.used
    result["limits"]["source_objects_read"] = env.reads
    result["limits"]["request_local_cache_hits"] = env.cache_hits
    if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_RESPONSE_BYTES:
        fail("RESPONSE_BUDGET", 413)
    return result, 200


async def handle(env, request):
    try:
        return await asyncio.wait_for(execute(env, request), 25)
    except PrivateFinancialsError as exc:
        return {"error": {"code": exc.code}}, exc.status
    except HistoryError as exc:
        return {"error": {"code": exc.code}}, 422
    except (ValueError, KeyError, TypeError):
        return {"error": {"code": "INVALID_SOURCE_OR_PARAMETER"}}, 422
    except TimeoutError:
        return {"error": {"code": "QUERY_TIMEOUT"}}, 504
    except Exception:
        return {"error": {"code": "RESEARCH_UNAVAILABLE"}}, 503
