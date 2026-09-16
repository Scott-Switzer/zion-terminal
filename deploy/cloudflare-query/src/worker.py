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

app = FastAPI(title="Zion Financial Truth Query", version="1.0.0", docs_url=None, redoc_url=None)
METRICS = ("revenue", "operating_margin", "last_price")
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


@app.get("/healthz")
async def healthz(): return {"status": "ok", "service": "zion-terminal", "runtime": "cloudflare-python-worker"}

@app.get("/readyz")
async def readyz(): return {"status": "ready", "service": "zion-terminal"}

@app.get("/v1/capabilities")
async def capabilities(request: Request):
    env = request.scope["env"]
    return {"schema_version": "1", "service_version": getattr(env, "SERVICE_VERSION", "staging"), "git_sha": "cloudflare-staging", "worlds": ["real", "synthetic"], "metrics": list(METRICS)}

@app.post("/v1/query")
async def query(request: Request):
    request_id = rid(request)
    if int(request.headers.get("content-length", "0") or 0) > 32000: return fail(request, "INVALID_REQUEST", "request exceeds 32KB", 400)
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {"query", "world", "as_of"} or not isinstance(body.get("world"), dict): raise ValueError("INVALID_REQUEST: request must contain query and world")
        symbol, metrics = plan(body["query"]); as_of = parse_as_of(body.get("as_of"))
        if body["world"].get("world_type") == "real": result = await real_query(request.scope["env"], symbol, metrics, as_of, request_id)
        elif body["world"].get("world_type") == "synthetic": result = await synthetic_query(request.scope["env"], symbol, metrics, as_of, request_id)
        else: return fail(request, "WORLD_NOT_FOUND", "world is not available", 404)
        if result["world"].get("world_id") != body["world"].get("world_id"): return fail(request, "WORLD_NOT_FOUND", "world is not available", 404)
        return result
    except FileNotFoundError: return fail(request, "RELEASE_NOT_AVAILABLE", "published release is unavailable", 503, True)
    except LookupError as error: return fail(request, str(error), str(error).replace("_", " ").lower(), 404 if str(error) != "WORLD_NOT_CERTIFIED" else 503)
    except ValueError as error:
        message = str(error); code, _, detail = message.partition(": ")
        return fail(request, code if code.isupper() else "INVALID_REQUEST", detail or message, 422 if code == "QUERY_NOT_SUPPORTED" else 400)
    except Exception:
        return fail(request, "UPSTREAM_UNAVAILABLE", "published data is temporarily unavailable", 503, True)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)
