from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlparse

from workers import Response, WorkerEntrypoint

from serving_v2 import (
    ServingV2Error,
    begin_telemetry,
    fundamentals,
    finish_telemetry,
    latest_price,
    price_history,
    query as serving_query,
)

REAL_WORLD = {"world_type": "real", "world_id": "us-public-markets"}
SYMBOLS = ("AAPL", "MSFT", "NVDA")
QUERY_WORDS = {"WHAT", "IS", "THE", "A", "AN", "AND", "OR", "SHOW", "GIVE", "ME", "ARE", "FOR", "REVENUE", "SALES", "OPERATING", "MARGIN", "LAST", "PRICE", "HISTORY", "QUARTERLY", "COMPARE"}


def request_id(request) -> str:
    value = request.headers.get("x-request-id", "")
    return value if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) else str(uuid.uuid4())


def add_telemetry(result: dict) -> dict:
    telemetry = finish_telemetry()
    result.setdefault("release", {})["serving_telemetry"] = telemetry
    return result


def tool_as_query(result: dict, rid: str) -> dict:
    data = result.get("data", {})
    observations = data.get("observations", []) if isinstance(data, dict) else []
    if "prices" in data:
        observations = [{"metric": "price_history", "value": data["prices"], "unit": "USD/share", "period": "daily"}]
    if not observations and result.get("evidence"):
        observations = result["evidence"]
    if not observations and isinstance(data, dict) and "series" in data:
        observations = [row for rows in data["series"].values() for row in rows]
    entity = {"symbol": "", "entity_id": ""}
    if observations and isinstance(observations[0], dict):
        entity = {"symbol": observations[0].get("symbol", ""), "entity_id": observations[0].get("entity_id", "")}
    return {
        "schema_version": "1",
        "request_id": rid,
        "world": result.get("world", {}),
        "entity": entity,
        "answer": f"Tool {result.get('tool')} returned verified data.",
        "metrics": [{key: row[key] for key in ("metric", "value", "unit", "period", "session") if key in row} for row in observations],
        "calculations": [],
        "evidence": result.get("evidence", observations),
        "quality": result.get("quality", {}),
        "release": result.get("release", {}),
    }


def symbols_in(text: str) -> list[str]:
    upper = text.upper()
    return [symbol for symbol in re.findall(r"\b[A-Z][A-Z0-9_-]{1,9}\b", upper) if symbol not in QUERY_WORDS]


def metrics_in(text: str) -> list[str]:
    upper = text.upper()
    metrics = []
    if "REVENUE" in upper or "SALES" in upper:
        metrics.append("revenue")
    if "OPERATING MARGIN" in upper or " MARGIN" in upper:
        metrics.append("operating_margin")
    if "PRICE" in upper:
        metrics.append("last_price")
    return metrics


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        begin_telemetry(self.env)
        rid = request_id(request)
        path = urlparse(request.url).path
        if request.method == "GET" and path in {"/healthz", "/readyz"}:
            return self._response({"status": "ok", "service": "zion-financial-serving-v2-thin", "runtime": "cloudflare-python-worker"})
        if request.method == "GET" and path == "/v1/capabilities":
            return self._response({"schema_version": "1", "service_version": getattr(self.env, "SERVICE_VERSION", "thin-staging"), "worlds": ["real"], "metrics": ["revenue", "operating_margin", "last_price"]})
        try:
            if request.method != "POST":
                return self._error(rid, "NOT_FOUND", "route not found", 404)
            body = await request.json()
            if not isinstance(body, dict) or len(json.dumps(body)) > 32000:
                return self._error(rid, "INVALID_REQUEST", "bounded JSON object required", 400)
            if path == "/v1/query":
                result = await self._query(body, rid)
            elif path.startswith("/v1/tools/"):
                result = await self._tool(path.rsplit("/", 1)[-1], body, rid)
            else:
                return self._error(rid, "NOT_FOUND", "route not found", 404)
            return self._response(add_telemetry(result))
        except ServingV2Error as error:
            return self._error(rid, error.code, error.message, 503 if error.retryable else 404, error.retryable)
        except LookupError as error:
            return self._error(rid, str(error), str(error).replace("_", " ").lower(), 404)
        except ValueError as error:
            message = str(error)
            code, _, detail = message.partition(": ")
            return self._error(rid, code if code.isupper() else "INVALID_REQUEST", detail or message, 400)
        except Exception:
            return self._error(rid, "UPSTREAM_UNAVAILABLE", "published data is temporarily unavailable", 503, True)

    async def _query(self, body: dict, rid: str) -> dict:
        world = body.get("world")
        if world != REAL_WORLD:
            raise LookupError("WORLD_NOT_FOUND")
        text = body.get("query")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("INVALID_REQUEST: query is required")
        upper = text.upper()
        symbols = symbols_in(text)
        if not symbols:
            raise ValueError("QUERY_NOT_SUPPORTED: an entity is required")
        if "PRICE HISTORY" in upper:
            result = await price_history(self.env, symbols[0], limit=500, as_of=body.get("as_of"), request_id=rid)
            return tool_as_query({"tool": "get_price_history", "world": result["world"], "data": {"prices": result["prices"]}, "evidence": [], "quality": {"status": "VERIFIED", "llm_required": False}, "release": result["release"]}, rid)
        if "COMPARE" in upper and len(symbols) >= 2:
            metric = "operating_margin" if "MARGIN" in upper else "revenue"
            series = {}
            releases = {}
            for symbol in symbols[:10]:
                result = await fundamentals(self.env, symbol, [metric], period="quarterly" if "QUARTER" in upper else None, lookback=4 if "QUARTER" in upper else 1, as_of=body.get("as_of"), request_id=rid)
                series[symbol] = result["observations"]
                releases[symbol] = result["release"]
            return tool_as_query({"tool": "compare", "world": world, "data": {"entities": symbols[:10], "metric": metric, "series": series}, "evidence": [row for rows in series.values() for row in rows], "quality": {"status": "VERIFIED"}, "release": releases[symbols[-1]]}, rid)
        metrics = metrics_in(text)
        if "QUARTER" in upper or "REVENUE GROW" in upper or "GROSS MARGIN" in upper:
            metric = "operating_margin" if "OPERATING MARGIN" in upper else "revenue"
            result = await fundamentals(self.env, symbols[0], [metric], period="quarterly", lookback=40 if "GROW" in upper else 8, as_of=body.get("as_of"), request_id=rid)
            return tool_as_query({"tool": "get_fundamentals", "world": result["world"], "data": {"observations": result["observations"]}, "evidence": result["observations"], "quality": {"status": "VERIFIED", "llm_required": False}, "release": result["release"]}, rid)
        if not metrics:
            raise ValueError("QUERY_NOT_SUPPORTED: supported metrics are revenue, operating margin, and last price")
        return await serving_query(self.env, symbols[0], metrics, as_of=body.get("as_of"), request_id=rid)

    async def _tool(self, name: str, body: dict, rid: str) -> dict:
        world = body.get("world") or REAL_WORLD
        if world != REAL_WORLD:
            raise LookupError("WORLD_NOT_FOUND")
        symbol = str(body.get("entity", body.get("symbol", ""))).upper()
        if name == "get_fundamentals":
            metrics = body.get("metrics") or [body.get("metric", "revenue")]
            result = await fundamentals(self.env, symbol, metrics, period=body.get("period"), lookback=min(int(body.get("lookback", 40)), 40), as_of=body.get("as_of"), request_id=rid)
            return {"tool": name, "world": result["world"], "data": {"observations": result["observations"]}, "evidence": result["observations"], "quality": {"status": "VERIFIED", "llm_required": False}, "release": result["release"]}
        if name == "get_price":
            result = await latest_price(self.env, symbol, as_of=body.get("as_of"), request_id=rid)
            return {"tool": name, "world": result["world"], "data": result["metric"], "evidence": [result["metric"]], "quality": {"status": "VERIFIED", "llm_required": False}, "release": result["release"]}
        if name == "get_price_history":
            result = await price_history(self.env, symbol, limit=min(int(body.get("limit", 500)), 500), start_date=body.get("start_date"), end_date=body.get("end_date"), as_of=body.get("as_of"), request_id=rid)
            return {"tool": name, "world": result["world"], "data": {"prices": result["prices"]}, "evidence": [], "quality": {"status": "VERIFIED", "llm_required": False}, "release": result["release"]}
        if name == "get_evidence":
            result = await fundamentals(self.env, symbol, [body.get("metric", "revenue")], period=body.get("period"), lookback=40, as_of=body.get("as_of"), request_id=rid)
            return {"tool": name, "world": result["world"], "data": {"evidence": result["observations"]}, "evidence": result["observations"], "quality": {"status": "VERIFIED"}, "release": result["release"]}
        if name == "compare":
            metric = body.get("metric", "operating_margin")
            series = {}
            release = {}
            for item in body.get("entities", []):
                result = await fundamentals(self.env, str(item).upper(), [metric], period=body.get("period"), lookback=min(int(body.get("lookback", 1)), 40), as_of=body.get("as_of"), request_id=rid)
                series[str(item).upper()] = result["observations"]
                release[str(item).upper()] = result["release"]
            return {"tool": name, "world": world, "data": {"entities": body.get("entities", []), "metric": metric, "series": series}, "evidence": [row for rows in series.values() for row in rows], "quality": {"status": "VERIFIED"}, "release": release}
        if name == "calculate":
            values = body.get("values", [])
            operation = body.get("operation")
            if operation == "change" and len(values) == 2: value = values[1] - values[0]
            elif operation == "percent_change" and len(values) == 2: value = (values[1] - values[0]) / values[0] if values[0] else None
            elif operation == "average" and values: value = sum(values) / len(values)
            else: raise ValueError("CALCULATION_NOT_SUPPORTED: operation or inputs are invalid")
            return {"tool": name, "world": world, "data": {"operation": operation, "value": value, "inputs": values, "formula": operation}, "evidence": [], "quality": {"status": "CALCULATED"}}
        raise LookupError("TOOL_NOT_FOUND")

    def _response(self, value: dict, status: int = 200):
        telemetry = finish_telemetry()
        headers = {"content-type": "application/json", "x-serving-telemetry": json.dumps(telemetry, separators=(",", ":"))}
        return Response(json.dumps(value, separators=(",", ":")), status=status, headers=headers)

    def _error(self, rid: str, code: str, message: str, status: int, retryable: bool = False):
        return self._response({"error": {"request_id": rid, "code": code, "message": message, "retryable": retryable}}, status)
