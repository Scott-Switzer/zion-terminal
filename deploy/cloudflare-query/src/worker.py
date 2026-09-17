from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from workers import WorkerEntrypoint, asgi

from serving_v2 import ServingV2Error, begin_telemetry as serving_v2_begin_telemetry, enabled as serving_v2_enabled, finish_telemetry as serving_v2_finish_telemetry, fundamentals as serving_v2_fundamentals, latest_price as serving_v2_latest_price, price_history as serving_v2_price_history, query as serving_v2_query, telemetry_snapshot as serving_v2_telemetry

app = FastAPI(title="Zion Financial Truth Query", version="1.0.0", docs_url=None, redoc_url=None)


@app.middleware("http")
async def serving_timing_headers(request: Request, call_next):
    serving_v2_begin_telemetry(request.scope.get("env"))
    response = await call_next(request)
    telemetry = serving_v2_finish_telemetry()
    if telemetry:
        response.headers["x-serving-telemetry"] = json.dumps(telemetry, separators=(",", ":"), sort_keys=True)
        response.headers["x-serving-r2-gets"] = str(telemetry.get("r2_gets", 0))
        response.headers["x-serving-cache-hits"] = str(telemetry.get("cache_hits", 0))
        response.headers["x-serving-cache-misses"] = str(telemetry.get("cache_misses", 0))
        if telemetry.get("isolate_instance_id"):
            response.headers["x-serving-isolate-id"] = telemetry["isolate_instance_id"]
            response.headers["x-serving-isolate-seq"] = str(telemetry["isolate_request_seq"])
            response.headers["x-serving-first-request-at"] = telemetry["isolate_first_request_at"]
    return response
METRICS = ("revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "gross_margin", "operating_margin", "net_margin", "cash", "assets", "liabilities", "equity", "debt", "shares_outstanding", "eps_diluted", "last_price")
ALIASES = {
    "revenue": "revenue", "sales": "revenue", "operating margin": "operating_margin",
    "operating_margin": "operating_margin", "margin": "operating_margin",
    "last price": "last_price", "last_price": "last_price", "price": "last_price",
}
STOPWORDS = {"GIVE", "ME", "WHAT", "ARE", "IS", "THE", "FOR", "AND", "OF", "SHOW", "REVENUE", "SALES", "OPERATING", "MARGIN", "LAST", "PRICE"}


def rid(request: Request) -> str:
    value = request.headers.get("x-request-id", "")
    return value if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) else str(uuid.uuid4())


def fail(request: Request, code: str, message: str, status: int, retryable: bool = False):
    return JSONResponse(status_code=status, content={"error": {"request_id": rid(request), "code": code, "message": message, "retryable": retryable}})


def plan(text: Any):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("INVALID_REQUEST: query must be a non-empty string under 2000 characters")
    upper = text.upper()
    metrics = []
    for phrase, metric in sorted(ALIASES.items(), key=lambda item: -len(item[0])):
        if phrase.upper() in upper and metric not in metrics:
            metrics.append(metric)
    if not metrics:
        raise ValueError("QUERY_NOT_SUPPORTED: supported metrics are revenue, operating margin, and last price")
    tokens = [token.strip(".,?!:;()[]{}") for token in upper.split()]
    symbols = [token for token in tokens if re.fullmatch(r"[A-Z][A-Z0-9_-]{1,9}", token) and token not in STOPWORDS]
    if not symbols:
        raise ValueError("QUERY_NOT_SUPPORTED: a security or synthetic company symbol is required")
    return symbols[-1], metrics


def iso(value: str) -> str:
    return value if "T" in value else f"{value}T00:00:00Z"


def before(value: str | None, as_of: datetime | None) -> bool:
    if as_of is None:
        return True
    if not value:
        return False
    return datetime.fromisoformat(iso(value).replace("Z", "+00:00")) <= as_of


def parse_as_of(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("INVALID_REQUEST: as_of must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("INVALID_REQUEST: as_of must be a valid ISO-8601 timestamp") from exc


async def text_object(bucket: Any, key: str) -> str:
    obj = await bucket.get(key)
    if obj is None:
        raise FileNotFoundError(key)
    return await obj.text()


def safe_key(prefix: str, suffix: str) -> str:
    if not prefix or prefix.startswith("/") or any(part in {"", ".", ".."} for part in prefix.split("/")):
        raise ValueError("invalid configured prefix")
    if not suffix or "\\" in suffix or any(part in {"", ".", ".."} for part in suffix.split("/")):
        raise ValueError("invalid artifact key")
    return f"{prefix.rstrip('/')}/{suffix}"


def select_pit(rows: list[dict], as_of: datetime | None) -> list[dict]:
    """Select latest eligible revision per canonical period identity."""
    eligible = [row for row in rows if before(row.get("available_at"), as_of)]
    groups = {}
    for row in eligible:
        key = tuple(row.get(k, "") for k in ("entity_id", "metric", "period", "fiscal_year", "fiscal_quarter", "period_start", "period_end", "unit"))
        groups.setdefault(key, []).append(row)
    return [max(group, key=lambda row: (row.get("available_at", ""), row.get("form") == "10-Q/A", row.get("accession", ""))) for group in groups.values()]


def evidence_response(world: dict, entity: dict, rows: list[dict], release: dict, request_id: str) -> dict:
    by_metric = {row["metric"]: row for row in rows}
    metrics = [{"metric": r["metric"], "value": r["value"], "unit": r["unit"], "period": r["period"]} for r in rows]
    clauses = []
    if "revenue" in by_metric:
        clauses.append(f"{entity['symbol']} revenue was ${by_metric['revenue']['value'] / 1_000_000_000:.3f}B")
    if "operating_margin" in by_metric:
        clauses.append(f"operating margin was {by_metric['operating_margin']['value']:.2%}")
    if "last_price" in by_metric:
        clauses.append(f"the latest published price was ${by_metric['last_price']['value']:.2f}")
    return {"schema_version": "1", "request_id": request_id, "world": world, "entity": entity,
            "answer": "; ".join(clauses) + ".", "metrics": metrics,
            "calculations": [r["calculation"] for r in rows if r.get("calculation")],
            "evidence": rows, "quality": {"status": "VERIFIED", "llm_required": False,
            "source_count": len(rows), "qc_status": release.get("qc_status")}, "release": release}


async def precomputed_real_query(env: Any, symbol: str, metrics: list[str], as_of: datetime | None, request_id: str) -> dict:
    if symbol not in {"AAPL", "MSFT", "NVDA"}:
        raise LookupError("ENTITY_NOT_FOUND")
    current = json.loads(await text_object(env.MARKET_DATA, "control/market-terminal/CURRENT.json"))
    pointer_key = getattr(env, "FUNDAMENTALS_CURRENT_KEY", "control/market-terminal/fundamentals/CURRENT.json")
    pointer = json.loads(await text_object(env.MARKET_DATA, pointer_key))
    if pointer.get("base_release") != current.get("prefix"):
        raise LookupError("RELEASE_NOT_AVAILABLE")
    prefix = pointer["prefix"]
    artifact = json.loads(await text_object(env.MARKET_DATA, safe_key(prefix, f"{symbol}.json")))
    summary = json.loads(await text_object(env.MARKET_DATA, safe_key(current["prefix"], f"securities/{symbol}/market_summary.json")))
    rows = []
    source_rows = artifact.get("observations", []) + artifact.get("revision_observations", [])
    enriched = [{
        **row, "entity_id": artifact["entity_id"],
        "world": {"world_type": "real", "world_id": "us-public-markets", "version": current["prefix"]},
        "provenance": {"producer": "Project-Portfolio-Engine", "artifact": f"{prefix}/{symbol}.json", "release_id": prefix, "base_release": current["prefix"]}
    } for row in source_rows if row.get("metric") in metrics]
    rows.extend(select_pit(enriched, as_of))
    if "last_price" in metrics:
        prices = artifact.get("price_history", [])
        available = [row for row in prices if before(row.get("available_at"), as_of)]
        if available:
            price = available[-1]
            rows.append({"entity_id": artifact["entity_id"], "metric": "last_price", "value": price["close"], "unit": "USD/share", "period": price["session"], "observation_at": price["session"] + "T21:00:00Z", "available_at": price["available_at"], "retrieved_at": price["available_at"], "source": "PPE-published-release", "source_record": f"{current['prefix']}/securities/{symbol}/price_history.parquet", "calculation": None, "world": {"world_type": "real", "world_id": "us-public-markets", "version": current["prefix"]}, "provenance": {"producer": "Project-Portfolio-Engine", "artifact": f"{current['prefix']}/securities/{symbol}/price_history.parquet", "release_id": current["prefix"]}, "quality": {"status": "observed"}})
    if len({row["metric"] for row in rows}) < len(metrics): raise LookupError("METRIC_NOT_AVAILABLE")
    entity = {"entity_id": artifact["entity_id"], "display_name": summary.get("company_name", symbol), "symbol": symbol}
    release = {"release_id": prefix, "base_release": current["prefix"], "qc_status": "VERIFIED"}
    return evidence_response({"world_type": "real", "world_id": "us-public-markets", "version": current["prefix"]}, entity, rows, release, request_id)


async def real_query(env: Any, symbol: str, metrics: list[str], as_of: datetime | None, request_id: str) -> dict:
    current = json.loads(await text_object(env.MARKET_DATA, "control/market-terminal/CURRENT.json"))
    prefix = current["prefix"]
    if symbol != "AAPL":
        raise LookupError("ENTITY_NOT_FOUND")
    summary_key = safe_key(prefix, "securities/AAPL/market_summary.json")
    summary = json.loads(await text_object(env.MARKET_DATA, summary_key))
    corpus_prefix = getattr(env, "SEC_CORPUS_PREFIX", "")
    manifest = json.loads(await text_object(env.SEC_CORPUS, safe_key(corpus_prefix, "corpus_manifest.json")))
    artifacts = manifest.get("artifacts", [])
    candidates = [a["path"] for a in artifacts if isinstance(a, dict) and isinstance(a.get("path"), str)
                  and "cik=0000320193" in a["path"] and a["path"].endswith("/primary.html")]
    if not candidates:
        raise LookupError("METRIC_NOT_AVAILABLE")
    html = await text_object(env.SEC_CORPUS, safe_key(corpus_prefix, candidates[-1]))
    contexts = {}
    for match in re.finditer(r'<xbrli:context\b[^>]*id=["\']([^"\']+)["\'][^>]*>(.*?)</xbrli:context>', html, re.I | re.S):
        cid, body = match.groups()
        start = re.search(r'<xbrli:startDate>([^<]+)', body, re.I)
        end = re.search(r'<xbrli:endDate>([^<]+)', body, re.I)
        contexts[cid] = (start.group(1) if start else "", end.group(1) if end else "", "segment" not in body.lower())
    facts = {"revenue": [], "operating_income": []}
    for match in re.finditer(r'<ix:nonFraction\b(?P<a>[^>]*)>(?P<v>[^<]*)</ix:nonFraction>', html, re.I | re.S):
        attrs = dict(re.findall(r'(\w[\w:-]*)\s*=\s*["\']([^"\']*)["\']', match.group("a")))
        name = attrs.get("name", "")
        metric = "revenue" if name.endswith(("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", ":Revenues")) else "operating_income" if name.endswith(":OperatingIncomeLoss") else None
        if metric is None or attrs.get("contextRef") not in contexts:
            continue
        start, end, consolidated = contexts[attrs["contextRef"]]
        if not start or not consolidated:
            continue
        try:
            value = float(match.group("v").replace(",", "")) * 10 ** int(attrs.get("scale", "0"))
            if attrs.get("sign") == "-": value = -abs(value)
        except ValueError:
            continue
        facts[metric].append((end, value, name))
    selected = {metric: max(rows, key=lambda row: (row[0], row[1])) for metric, rows in facts.items() if rows}
    if not selected.get("revenue") or not selected.get("operating_income"):
        raise LookupError("METRIC_NOT_AVAILABLE")
    filing_manifest_key = candidates[-1].removesuffix("primary.html") + "manifest.json"
    filing_manifest = json.loads(await text_object(env.SEC_CORPUS, safe_key(corpus_prefix, filing_manifest_key)))
    available = filing_manifest.get("filing_date")
    if not isinstance(available, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", available):
        raise LookupError("METRIC_NOT_AVAILABLE")
    rows = []
    def add(metric, value, unit, observation, calculation, source_record):
        if before(available, as_of):
            rows.append({"entity_id": f"real:equity:{summary['security_id']}", "metric": metric, "value": value, "unit": unit,
                         "period": observation[:10], "observation_at": iso(observation), "available_at": iso(available),
                         "retrieved_at": datetime.now(timezone.utc).isoformat(), "source": "PPE-published-release+SEC",
                         "source_record": source_record, "calculation": calculation, "world": {"world_type": "real", "world_id": "us-public-markets", "version": prefix},
                         "provenance": {"producer": "Project-Portfolio-Engine", "release_id": prefix, "artifact": summary_key,
                         "corpus_artifact": candidates[-1], "artifact_sha256": current.get("content_sha256")}, "quality": {"status": "calculated" if calculation else "observed"}})
    revenue = selected["revenue"]; operating = selected["operating_income"]
    if "revenue" in metrics: add("revenue", revenue[1], "USD", revenue[0], None, revenue[2])
    if "operating_margin" in metrics: add("operating_margin", operating[1] / revenue[1], "ratio", revenue[0], "operating_income / revenue", operating[2])
    if "last_price" in metrics: add("last_price", summary["latest_close"], "USD/share", summary["latest_date"], None, summary_key)
    if len(rows) < len(metrics): raise LookupError("METRIC_NOT_AVAILABLE")
    return evidence_response({"world_type": "real", "world_id": "us-public-markets", "version": prefix}, {"entity_id": f"real:equity:{summary['security_id']}", "display_name": summary["company_name"], "symbol": "AAPL"}, rows, {"release_id": prefix, "release_version": current.get("version"), "qc_status": "VERIFIED"}, request_id)


async def synthetic_query(env: Any, symbol: str, metrics: list[str], as_of: datetime | None, request_id: str) -> dict:
    world_id = getattr(env, "SYNTHETIC_WORLD_ID", "test-world-001")
    pointer_key = getattr(env, "SYNTHETIC_CURRENT_KEY", f"control/synthetic-worlds/{world_id}/CURRENT.json")
    pointer = json.loads(await text_object(env.MARKET_DATA, pointer_key))
    prefix = pointer["prefix"]
    manifest = json.loads(await text_object(env.MARKET_DATA, safe_key(prefix, "manifest.json")))
    if manifest.get("qc_status") != "PASS": raise LookupError("WORLD_NOT_CERTIFIED")
    entities = json.loads(await text_object(env.MARKET_DATA, safe_key(prefix, "public/entities.json")))["entities"]
    entity = next((item for item in entities if item["symbol"].upper() == symbol), None)
    if entity is None: raise LookupError("ENTITY_NOT_FOUND")
    financials = json.loads(await text_object(env.MARKET_DATA, safe_key(prefix, "public/financials.json")))["observations"]
    prices = json.loads(await text_object(env.MARKET_DATA, safe_key(prefix, "public/prices.json")))["prices"]
    rows = [row for row in financials if row["entity_id"] == entity["entity_id"] and row["metric"] in metrics and before(row.get("available_at"), as_of)]
    if "last_price" in metrics:
        available_prices = [row for row in prices if row["security"] == symbol and before(row.get("available_at"), as_of)]
        if available_prices:
            row = available_prices[-1]
            rows.append({"entity_id": entity["entity_id"], "metric": "last_price", "value": row["close"], "unit": row.get("unit", "USD/share"), "period": row["session"], "observation_at": row["observation_at"], "available_at": row["available_at"], "retrieved_at": row["available_at"], "source": "market-fuzzer-public-release", "source_record": row["provenance"]["source_record"], "calculation": None, "world": manifest["world"], "provenance": {"producer": manifest["producer"], "artifact": f"{prefix}/public/prices.json"}, "quality": {"status": "observed"}})
    if len({row["metric"] for row in rows}) < len(metrics): raise LookupError("METRIC_NOT_AVAILABLE")
    return evidence_response(manifest["world"], {key: entity[key] for key in ("entity_id", "display_name", "symbol")}, rows, {"world_version": manifest["world"].get("version"), "producer_sha": manifest["producer"].get("git_sha"), "qc_status": manifest.get("qc_status"), "release_id": prefix}, request_id)


def add_serving_telemetry(result: dict) -> dict:
    telemetry = serving_v2_telemetry()
    if telemetry:
        result.setdefault("release", {})["serving_telemetry"] = telemetry
    return result


async def tool_result(env: Any, name: str, body: dict, request_id: str) -> dict:
    world = body.get("world") or {"world_type": "real", "world_id": "us-public-markets"}
    if serving_v2_enabled(env) and world.get("world_type") == "real":
        entity = str(body.get("entity", body.get("symbol", ""))).upper()
        as_of = body.get("as_of")
        if name == "get_fundamentals":
            result = await serving_v2_fundamentals(env, entity, body.get("metrics", []), period=body.get("period"), lookback=int(body.get("lookback", 40)), as_of=as_of, request_id=request_id)
            return add_serving_telemetry({"tool": name, "world": result["world"], "data": {"observations": result["observations"]}, "evidence": result["observations"], "quality": {"status": "VERIFIED", "llm_required": False}, "release": result["release"]})
        if name == "get_price":
            result = await serving_v2_latest_price(env, entity, as_of=as_of, request_id=request_id)
            return add_serving_telemetry({"tool": name, "world": result["world"], "data": result["metric"], "evidence": [result["metric"]], "quality": {"status": "VERIFIED", "llm_required": False}, "release": result["release"]})
        if name == "get_price_history":
            result = await serving_v2_price_history(env, entity, limit=min(int(body.get("limit", 500)), 500), start_date=body.get("start_date"), end_date=body.get("end_date"), as_of=as_of, request_id=request_id)
            return add_serving_telemetry({"tool": name, "world": result["world"], "data": {"prices": result["prices"]}, "evidence": [], "quality": {"status": "VERIFIED", "llm_required": False}, "release": result["release"]})
        if name == "get_evidence":
            result = await serving_v2_fundamentals(env, entity, [body.get("metric", "revenue")], period=body.get("period"), lookback=40, as_of=as_of, request_id=request_id)
            return add_serving_telemetry({"tool": name, "world": result["world"], "data": {"evidence": result["observations"]}, "evidence": result["observations"], "quality": {"status": "VERIFIED"}, "release": result["release"]})
        if name == "compare":
            series = {}
            for item in body.get("entities", []):
                item_result = await serving_v2_fundamentals(env, str(item).upper(), [body.get("metric", "operating_margin")], period=body.get("period"), lookback=1, as_of=as_of, request_id=request_id)
                series[str(item).upper()] = item_result["observations"]
            return add_serving_telemetry({"tool": name, "world": {"world_type": "real", "world_id": "us-public-markets"}, "data": {"entities": body.get("entities", []), "metric": body.get("metric", "operating_margin"), "series": series}, "evidence": [row for rows in series.values() for row in rows], "quality": {"status": "VERIFIED"}, "release": item_result["release"] if series else {}})
    symbol = str(body.get("entity", body.get("symbol", ""))).upper()
    as_of = parse_as_of(body.get("as_of"))
    metrics = body.get("metrics") or [body.get("metric", "revenue")]
    if not isinstance(metrics, list) or len(metrics) > 20: raise ValueError("INVALID_TOOL_ARGUMENT: metrics must contain at most 20 items")
    if any(metric not in METRICS for metric in metrics): raise ValueError("METRIC_NOT_SUPPORTED: requested metric is not registered")
    if name == "resolve_entity":
        if world.get("world_type") == "real" and symbol in {"AAPL", "MSFT", "NVDA"}:
            return {"tool": name, "world": world, "data": {"entity_id": f"real:equity:{symbol}", "symbol": symbol, "name": symbol, "match_type": "symbol", "confidence": 1.0}, "evidence": [], "quality": {"status": "VERIFIED"}}
        if world.get("world_type") == "synthetic":
            snap = await synthetic_query(env, symbol, ["revenue"], as_of, request_id)
            return {"tool": name, "world": world, "data": snap["entity"], "evidence": [], "quality": snap["quality"], "release": snap["release"]}
        raise LookupError("ENTITY_NOT_FOUND")
    if name in {"get_price", "get_price_history", "get_fundamentals", "get_filing"}:
        if name == "get_fundamentals":
            result = await (precomputed_real_query(env, symbol, metrics, as_of, request_id) if world.get("world_type") == "real" else synthetic_query(env, symbol, metrics, as_of, request_id))
            if body.get("period") in {"annual", "quarterly"}: result["evidence"] = [row for row in result["evidence"] if row.get("period") == body["period"]]
            if body.get("lookback"): result["evidence"] = result["evidence"][-min(int(body["lookback"]), 40):]
            if not result["evidence"]: raise LookupError("PERIOD_NOT_AVAILABLE")
            return {"tool": name, "world": result["world"], "data": {"observations": result["evidence"]}, "evidence": result["evidence"], "quality": result["quality"], "release": result["release"]}
        if name == "get_filing":
            if world.get("world_type") != "real": raise LookupError("TOOL_NOT_SUPPORTED_FOR_WORLD")
            result = await precomputed_real_query(env, symbol, ["revenue"], as_of, request_id)
            filings = {}
            for row in result["evidence"]: filings[(row.get("form"), row.get("accession"))] = {"form": row.get("form"), "filing_date": row.get("filing_date"), "period_end": row.get("period_end"), "accession": row.get("accession"), "available_at": row.get("available_at"), "artifact": row.get("provenance", {}).get("artifact")}
            return {"tool": name, "world": result["world"], "data": {"filings": list(filings.values())[:min(int(body.get("limit", 20)), 20)]}, "evidence": result["evidence"], "quality": result["quality"], "release": result["release"]}
        if name == "get_price_history":
            limit = min(int(body.get("limit", 500)), 500)
            if world.get("world_type") == "real":
                current = json.loads(await text_object(env.MARKET_DATA, "control/market-terminal/CURRENT.json")); pointer = json.loads(await text_object(env.MARKET_DATA, getattr(env, "FUNDAMENTALS_CURRENT_KEY", "control/market-terminal/fundamentals/CURRENT.json"))); artifact = json.loads(await text_object(env.MARKET_DATA, safe_key(pointer["prefix"], f"{symbol}.json")))
                prices = [row for row in artifact.get("price_history", []) if before(row.get("available_at"), as_of)]
                return {"tool": name, "world": world, "data": {"prices": prices[-limit:]}, "evidence": [], "quality": {"status": "VERIFIED"}, "release": {"release_id": current["prefix"], "derived_release": pointer["prefix"]}}
            if world.get("world_type") == "synthetic":
                world_id = getattr(env, "SYNTHETIC_WORLD_ID", "test-world-001"); pointer = json.loads(await text_object(env.MARKET_DATA, getattr(env, "SYNTHETIC_CURRENT_KEY", f"control/synthetic-worlds/{world_id}/CURRENT.json"))); prefix = pointer["prefix"]; manifest = json.loads(await text_object(env.MARKET_DATA, safe_key(prefix, "manifest.json")))
                if manifest.get("qc_status") != "PASS": raise LookupError("WORLD_NOT_CERTIFIED")
                prices = json.loads(await text_object(env.MARKET_DATA, safe_key(prefix, "public/prices.json")))["prices"]
                prices = [row for row in prices if row.get("security") == symbol and before(row.get("available_at"), as_of)]
                return {"tool": name, "world": world, "data": {"prices": prices[-limit:]}, "evidence": [], "quality": {"status": "VERIFIED", "qc_status": "PASS"}, "release": {"release_id": prefix}}
            raise LookupError("WORLD_NOT_FOUND")
        result = await (precomputed_real_query(env, symbol, ["last_price"], as_of, request_id) if world.get("world_type") == "real" else synthetic_query(env, symbol, ["last_price"], as_of, request_id))
        return {"tool": name, "world": result["world"], "data": result["metrics"][0], "evidence": result["evidence"], "quality": result["quality"], "release": result["release"]}
    if name == "get_evidence":
        metric = body.get("metric")
        if world.get("world_type") != "real":
            result = await synthetic_query(env, symbol, [metric] if metric else ["revenue"], as_of, request_id)
        else:
            result = await precomputed_real_query(env, symbol, [metric] if metric else ["revenue"], as_of, request_id)
        rows = [row for row in result["evidence"] if not metric or row.get("metric") == metric]
        if not rows: raise LookupError("EVIDENCE_NOT_FOUND")
        return {"tool": name, "world": result["world"], "data": {"evidence": rows}, "evidence": rows, "quality": result["quality"], "release": result["release"]}
    if name == "calculate":
        operation = body.get("operation"); values = body.get("values", [])
        if not isinstance(values, list) or len(values) > 40: raise ValueError("INVALID_TOOL_ARGUMENT: values are bounded")
        if operation == "percent_change" and len(values) == 2: value = (values[1] - values[0]) / values[0] if values[0] else None
        elif operation == "change" and len(values) == 2: value = values[1] - values[0]
        elif operation == "average" and values: value = sum(values) / len(values)
        elif operation == "min" and values: value = min(values)
        elif operation == "max" and values: value = max(values)
        elif operation == "basis_point_change" and len(values) == 2: value = (values[1] - values[0]) * 10000
        else: raise ValueError("CALCULATION_NOT_SUPPORTED: allowlisted operation or inputs are invalid")
        return {"tool": name, "world": world, "data": {"operation": operation, "value": value, "inputs": values, "formula": operation}, "evidence": body.get("evidence", []), "quality": {"status": "CALCULATED"}}
    if name == "compare":
        entities = body.get("entities", []); metric = body.get("metric", "operating_margin")
        if not isinstance(entities, list) or len(entities) > 10: raise ValueError("RESULT_LIMIT_EXCEEDED: compare supports at most 10 entities")
        series = {}; releases = {}
        for item in entities:
            result = await (precomputed_real_query(env, str(item).upper(), [metric], as_of, request_id) if world.get("world_type") == "real" else synthetic_query(env, str(item).upper(), [metric], as_of, request_id))
            rows = result["evidence"]
            if body.get("period") in {"annual", "quarterly"}: rows = [row for row in rows if row.get("period") == body["period"]]
            rows.sort(key=lambda row: (row.get("fiscal_year", 0), row.get("fiscal_quarter") or "", row.get("period_end", "")))
            if body.get("lookback"): rows = rows[-min(int(body["lookback"]), 40):]
            series[str(item).upper()] = rows; releases[str(item).upper()] = result.get("release", {})
        return {"tool": name, "world": world, "data": {"entities": entities, "metric": metric, "series": series}, "evidence": [row for rows in series.values() for row in rows], "quality": {"status": "VERIFIED"}, "release": releases}
    raise LookupError("TOOL_NOT_FOUND")


@app.post("/v1/tools/{tool_name}")
async def tools(tool_name: str, request: Request):
    request_id = rid(request)
    serving_v2_begin_telemetry(request.scope.get("env"))
    try:
        body = await request.json()
        if not isinstance(body, dict) or len(json.dumps(body)) > 32000: raise ValueError("INVALID_REQUEST: bounded JSON object required")
        return await tool_result(request.scope["env"], tool_name, body, request_id)
    except FileNotFoundError: return fail(request, "RELEASE_NOT_AVAILABLE", "published release is unavailable", 503, True)
    except ServingV2Error as error: return fail(request, error.code, error.message, 503 if error.retryable else 404, error.retryable)
    except LookupError as error: return fail(request, str(error), str(error).replace("_", " ").lower(), 404 if str(error) != "TOOL_NOT_SUPPORTED_FOR_WORLD" else 422)
    except ValueError as error:
        message = str(error); code, _, detail = message.partition(": "); return fail(request, code if code.isupper() else "INVALID_TOOL_ARGUMENT", detail or message, 422 if code in {"CALCULATION_NOT_SUPPORTED", "INVALID_TOOL_ARGUMENT"} else 400)
    except Exception: return fail(request, "UPSTREAM_UNAVAILABLE", "published data is temporarily unavailable", 503, True)


@app.get("/healthz")
async def healthz(): return {"status": "ok", "service": "zion-terminal", "runtime": "cloudflare-python-worker"}

@app.get("/readyz")
async def readyz(): return {"status": "ready", "service": "zion-terminal"}

@app.get("/v1/capabilities")
async def capabilities(request: Request):
    env = request.scope["env"]
    return {"schema_version": "1", "service_version": getattr(env, "SERVICE_VERSION", "staging"), "git_sha": "cloudflare-staging", "worlds": ["real", "synthetic"], "metrics": list(METRICS), "tools": {name: {"supported_worlds": ["real", "synthetic"]} for name in ("resolve_entity", "get_price", "get_price_history", "get_fundamentals", "get_filing", "get_evidence", "calculate", "compare")}, "calculation_operations": ["change", "percent_change", "average", "min", "max", "basis_point_change"]}

def tool_as_query(result: dict, request_id: str) -> dict:
    data = result.get("data", {})
    observations = data.get("observations", []) if isinstance(data, dict) else []
    if "prices" in data: observations = [{"metric": "price_history", "value": data["prices"], "unit": "USD/share", "period": "daily"}]
    if "evidence" in data and not observations: observations = data["evidence"]
    if not observations and result.get("evidence"): observations = result["evidence"]
    if not observations and isinstance(data, dict) and "series" in data:
        observations = [row for rows in data["series"].values() for row in rows]
    entity = {"symbol": "", "entity_id": ""}
    if observations and isinstance(observations[0], dict): entity = {"symbol": observations[0].get("symbol", ""), "entity_id": observations[0].get("entity_id", "")}
    return {"schema_version": "1", "request_id": request_id, "world": result.get("world", {}), "entity": entity, "answer": f"Tool {result.get('tool')} returned verified data.", "metrics": [{k: row[k] for k in ("metric", "value", "unit", "period", "session") if k in row} for row in observations], "calculations": [], "evidence": result.get("evidence", observations), "quality": result.get("quality", {}), "release": result.get("release", {})}


@app.post("/v1/query")
async def query(request: Request):
    request_id = rid(request)
    serving_v2_begin_telemetry(request.scope.get("env"))
    if int(request.headers.get("content-length", "0") or 0) > 32000: return fail(request, "INVALID_REQUEST", "request exceeds 32KB", 400)
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {"query", "world", "as_of"} or not isinstance(body.get("world"), dict): raise ValueError("INVALID_REQUEST: request must contain query and world")
        text = body["query"]; upper = text.upper(); as_of = parse_as_of(body.get("as_of"))
        symbols = [token for token in ("AAPL", "MSFT", "NVDA", "NOVA") if re.search(rf"\b{token}\b", upper)]
        world = body["world"]
        if world.get("world_type") == "real" and world.get("world_id") != "us-public-markets":
            raise LookupError("WORLD_NOT_FOUND")
        if world.get("world_type") == "synthetic" and world.get("world_id") != "test-world-001":
            raise LookupError("WORLD_NOT_FOUND")
        if world.get("world_type") not in {"real", "synthetic"}:
            raise LookupError("WORLD_NOT_FOUND")
        if "PRICE HISTORY" in upper:
            if not symbols: raise ValueError("QUERY_NOT_SUPPORTED: an entity is required")
            result = await tool_result(request.scope["env"], "get_price_history", {"entity": symbols[0], "world": world, "limit": 500, "as_of": body.get("as_of")}, request_id); return tool_as_query(result, request_id)
        if "COMPARE" in upper and len(symbols) >= 2:
            metric = "operating_margin" if "MARGIN" in upper else "revenue"; result = await tool_result(request.scope["env"], "compare", {"entities": symbols[:10], "metric": metric, "period": "quarterly" if "QUARTER" in upper else None, "lookback": 4 if "QUARTER" in upper else None, "world": world, "as_of": body.get("as_of")}, request_id); return tool_as_query(result, request_id)
        if "FILING" in upper or "10-Q" in upper or "10-K" in upper:
            if not symbols: raise ValueError("QUERY_NOT_SUPPORTED: an entity is required")
            result = await tool_result(request.scope["env"], "get_filing", {"entity": symbols[0], "world": world, "limit": 20, "as_of": body.get("as_of")}, request_id); return tool_as_query(result, request_id)
        if "GROSS MARGIN" in upper or "QUARTER" in upper or "REVENUE GROW" in upper:
            if not symbols: raise ValueError("QUERY_NOT_SUPPORTED: an entity is required")
            metric = "gross_margin" if "GROSS MARGIN" in upper else "operating_margin" if "OPERATING MARGIN" in upper else "revenue"; result = await tool_result(request.scope["env"], "get_fundamentals", {"entity": symbols[0], "metrics": [metric], "period": "quarterly" if "QUARTER" in upper or "GROW" in upper else None, "lookback": 40 if "GROW" in upper else 8, "world": world, "as_of": body.get("as_of")}, request_id)
            if "REVENUE GROW" in upper:
                rows = [r for r in result.get("evidence", []) if r.get("metric") == "revenue" and r.get("fiscal_quarter")]
                rows.sort(key=lambda r: (r.get("fiscal_year", 0), r.get("period_end", "")))
                latest = rows[-1] if rows else None
                prior = next((r for r in reversed(rows[:-1]) if r.get("fiscal_year") == latest.get("fiscal_year") - 1 and r.get("fiscal_quarter") == latest.get("fiscal_quarter")), None) if latest else None
                if not latest or not prior or not prior.get("value"):
                    raise LookupError("PERIOD_NOT_AVAILABLE")
                calc = {**latest, "metric": "revenue_yoy_growth", "value": (latest["value"] - prior["value"]) / prior["value"], "unit": "ratio", "source_type": "CALCULATED", "formula": "current fiscal quarter revenue / prior-year same fiscal quarter revenue - 1", "input_evidence_ids": [latest.get("evidence_id", ""), prior.get("evidence_id", "")], "input_accessions": [latest.get("accession", ""), prior.get("accession", "")], "available_at": max(latest.get("available_at", ""), prior.get("available_at", ""))}
                result["data"]["observations"] = [calc]
                result["evidence"] = [calc, latest, prior]
            return tool_as_query(result, request_id)
        symbol, metrics = plan(text)
        if serving_v2_enabled(request.scope["env"]) and world.get("world_type") == "real":
            result = add_serving_telemetry(await serving_v2_query(request.scope["env"], symbol, metrics, as_of=body.get("as_of"), request_id=request_id))
        elif world.get("world_type") == "real": result = await precomputed_real_query(request.scope["env"], symbol, metrics, as_of, request_id)
        elif body["world"].get("world_type") == "synthetic": result = await synthetic_query(request.scope["env"], symbol, metrics, as_of, request_id)
        else: return fail(request, "WORLD_NOT_FOUND", "world is not available", 404)
        if result["world"].get("world_id") != body["world"].get("world_id"): return fail(request, "WORLD_NOT_FOUND", "world is not available", 404)
        return result
    except FileNotFoundError: return fail(request, "RELEASE_NOT_AVAILABLE", "published release is unavailable", 503, True)
    except ServingV2Error as error: return fail(request, error.code, error.message, 503 if error.retryable else 404, error.retryable)
    except LookupError as error: return fail(request, str(error), str(error).replace("_", " ").lower(), 404 if str(error) != "WORLD_NOT_CERTIFIED" else 503)
    except ValueError as error:
        message = str(error); code, _, detail = message.partition(": ")
        return fail(request, code if code.isupper() else "INVALID_REQUEST", detail or message, 422 if code == "QUERY_NOT_SUPPORTED" else 400)
    except Exception:
        return fail(request, "UPSTREAM_UNAVAILABLE", "published data is temporarily unavailable", 503, True)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)
