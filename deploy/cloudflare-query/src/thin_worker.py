from __future__ import annotations

import hashlib
import json
import re
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from workers import Response, WorkerEntrypoint

from temporal_core import TEMPORAL_CONTRACT_SHA256, TEMPORAL_SCHEMA_VERSION
from serving_v2 import (
    ServingV2Error,
    begin_telemetry,
    fundamentals,
    corporate_actions,
    finish_telemetry,
    load_release,
    resolve_security,
    latest_price,
    price_history,
    query as serving_query,
    artifact,
    _as_of,
    _available,
    _serve_row,
)
from contract_v2 import (
    CONTRACT_VERSION,
    TOOLS,
    ContractError,
    calculate_exact,
    contract_sha256,
    success as contract_success,
    validate_object,
)

REAL_WORLD = {"world_type": "real", "world_id": "us-public-markets"}
_SCREEN_CACHE: dict[str, dict] = {}
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
            return self._response(self._capabilities_v1())
        if request.method == "GET" and path == "/v2/capabilities":
            return self._response(await self._capabilities_v2())
        if request.method == "POST" and path == "/mcp":
            return await self._mcp(request, rid)
        try:
            if request.method != "POST":
                return self._error(rid, "NOT_FOUND", "route not found", 404)
            body = await request.json()
            if not isinstance(body, dict) or len(json.dumps(body)) > 32000:
                return self._error(rid, "INVALID_REQUEST", "bounded JSON object required", 400)
            if path.startswith("/v2/tools/"):
                result = await self._tool_v2(path.rsplit("/", 1)[-1], body, rid)
            elif path == "/v1/query":
                result = await self._query(body, rid)
            elif path.startswith("/v1/tools/"):
                result = await self._tool(path.rsplit("/", 1)[-1], body, rid)
            else:
                return self._error(rid, "NOT_FOUND", "route not found", 404)
            return self._response(add_telemetry(result))
        except ContractError as error:
            return self._error(rid, error.code, error.message, error.status, error.retryable)
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
        if name == "resolve_security":
            result = await resolve_security(self.env, (await load_release(self.env))[1], symbol, body.get("as_of"))
            return {"tool": name, "world": world, "data": result, "evidence": [result.get("identity", {}).get("evidence_id")], "quality": {"status": "VERIFIED"}, "release": {"serving_release_id": result["serving_release_id"], "temporal_contract_hash": result["temporal_contract_sha256"]}}
        if name == "get_corporate_actions":
            result = await corporate_actions(self.env, symbol=symbol or None, entity_id=body.get("entity_id"), instrument_id=body.get("instrument_id"), start=body.get("start"), end=body.get("end"), as_of=body.get("as_of"), action_types=body.get("action_types"), request_id=rid)
            return {"tool": name, "world": result["world"], "data": result["data"], "evidence": result["evidence"], "quality": {"status": "VERIFIED"}, "release": result["release"]}
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

    def _capabilities_v1(self) -> dict:
        legacy = ("get_fundamentals", "get_price", "get_price_history", "get_evidence", "compare", "resolve_security", "get_corporate_actions")
        return {"schema_version": "1", "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_sha256": TEMPORAL_CONTRACT_SHA256, "temporal_contract_hash": TEMPORAL_CONTRACT_SHA256, "tool_contract_version": CONTRACT_VERSION, "tool_contract_sha256": contract_sha256(), "service_version": getattr(self.env, "SERVICE_VERSION", "thin-staging"), "git_sha": getattr(self.env, "GIT_SHA", "unknown"), "worlds": ["real"], "metrics": ["revenue", "operating_margin", "last_price"], "tools": {name: {"supported_worlds": TOOLS[name]["supported_worlds"]} for name in legacy}}

    async def _capabilities_v2(self) -> dict:
        try:
            _, manifest = await load_release(self.env)
            release_id = manifest.get("serving_release_id")
        except Exception:
            release_id = None
        return {"schema_version": "zion-tool-capabilities-v2", "service_version": getattr(self.env, "SERVICE_VERSION", "thin-staging"), "git_sha": getattr(self.env, "GIT_SHA", "unknown"), "tool_contract_version": CONTRACT_VERSION, "tool_contract_sha256": contract_sha256(), "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_sha256": TEMPORAL_CONTRACT_SHA256, "serving_release_id": release_id, "worlds": [{"world_type": "real", "world_id": "us-public-markets"}], "tools": [{"name": name, "title": definition["title"], "description": definition["description"], "input_schema": definition["input_schema"], "output_schema": definition["output_schema"], "read_only": True, "deterministic": True, "supported_worlds": definition["supported_worlds"]} for name, definition in TOOLS.items()]}

    async def _tool_v2(self, name: str, body: dict, rid: str) -> dict:
        definition = TOOLS.get(name)
        if definition is None:
            raise ContractError("TOOL_NOT_FOUND", "tool is not registered", status=404)
        if not isinstance(body.get("world"), dict) or not isinstance(body.get("arguments"), dict):
            raise ContractError("INVALID_REQUEST", "world and arguments are required")
        validate_object(body["world"], {"type": "object", "required": ["world_type", "world_id"], "properties": {"world_type": {"type": "string"}, "world_id": {"type": "string"}, "version": {"type": "string"}}, "additionalProperties": False})
        if body["world"].get("world_type") != "real" or body["world"].get("world_id") != "us-public-markets":
            raise ContractError("WORLD_NOT_FOUND", "world is not available", status=404)
        if body.get("serving_release_id") is not None:
            if not isinstance(body["serving_release_id"], str):
                raise ContractError("INVALID_ARGUMENT", "serving_release_id must be a release identifier")
            _, pinned_manifest = await load_release(self.env)
            if pinned_manifest.get("serving_release_id") != body["serving_release_id"]:
                raise ContractError("RELEASE_NOT_FOUND", "requested serving release is not CURRENT", status=404)
        validate_object(body["arguments"], definition["input_schema"])
        args = dict(body["arguments"])
        if body.get("as_of") is not None: args["as_of"] = body["as_of"]
        if body.get("serving_release_id") is not None: args["serving_release_id"] = body["serving_release_id"]
        if name == "calculate":
            if any(isinstance(value, float) for value in args["values"]):
                raise ContractError("INVALID_ARGUMENT", "calculator values must be JSON integers or decimal strings")
            data = calculate_exact(args["operation"], args["values"])
            return contract_success(name, data, world=body["world"], evidence=[], quality={"status": "CALCULATED", "warnings": [], "source_limitations": []}, provenance={"deterministic": True}, release=None, request_id=rid, sha=contract_sha256())
        if name == "screen":
            return await self._screen_v2(args, body["world"], rid)
        if name == "resolve_entity":
            symbol = args.get("symbol") or args.get("entity_id") or args.get("cik")
            if not symbol: raise ContractError("INVALID_ARGUMENT", "one of symbol, entity_id, or cik is required")
            result = await self._tool("resolve_security", {"world": body["world"], "symbol": symbol, "as_of": args.get("as_of")}, rid)
        elif name == "get_revision_history":
            result = await self._revision_history_v2(args, body["world"], rid)
        elif name == "get_evidence" and args.get("evidence_id"):
            result = await self._evidence_v2(args["evidence_id"], body["world"], rid)
        elif name == "get_evidence" and not (args.get("observation_id") or args.get("action_id")):
            raise ContractError("INVALID_ARGUMENT", "evidence_id is required")
        elif name == "get_evidence" and args.get("evidence_id"):
            result = await self._evidence_v2(args["evidence_id"], body["world"], rid)
        else:
            result = await self._tool(name, {**args, "world": body["world"]}, rid)
        if name == "get_price_history" and not result.get("evidence"):
            result["evidence"] = result.get("data", {}).get("prices", [])
        if name == "compare":
            series = result.get("data", {}).get("series", {})
            result["data"] = {"metric": result.get("data", {}).get("metric"), "series": [{"requested_identifier": identifier, "entity_id": next((row.get("entity_id") for row in observations if row.get("entity_id")), None), "observations": observations} for identifier, observations in series.items()]}
        result_release = result.get("release")
        if result_release is None and result.get("serving_release_id"):
            result_release = {"serving_release_id": result.get("serving_release_id"), "temporal_contract_sha256": result.get("temporal_contract_sha256")}
        return contract_success(name, result.get("data", {}), world=result.get("world", body["world"]), evidence=result.get("evidence", []), quality={"status": result.get("quality", {}).get("status", "VERIFIED"), "warnings": [], "source_limitations": []}, provenance={"request_id": rid}, release=result_release, request_id=rid, sha=contract_sha256())

    async def _revision_history_v2(self, args: dict, world: dict, rid: str) -> dict:
        symbol = args.get("symbol") or args.get("entity")
        if not symbol:
            raise ContractError("INVALID_ARGUMENT", "symbol or entity is required")
        _, manifest = await load_release(self.env)
        resolved = await resolve_security(self.env, manifest, str(symbol), args.get("as_of"))
        path = resolved["identity"]["artifact_path"]
        source = "annual" if args.get("period_type") == "annual" else "quarterly"
        rows = await artifact(self.env, manifest, f"{path}/fundamentals/{source}.json")
        cutoff = _as_of(args.get("as_of"))
        rows = [row for row in rows if row.get("metric_id") == args["metric"] and _available(row, cutoff)]
        if args.get("period_start"): rows = [row for row in rows if row.get("period_start") == args["period_start"]]
        if args.get("period_end"): rows = [row for row in rows if row.get("period_end") == args["period_end"]]
        rows.sort(key=lambda row: row.get("available_at", ""))
        revisions = [_serve_row(row, release_id=manifest["serving_release_id"], artifact_path=f"{path}/fundamentals/{source}.json", source_snapshot_id=manifest["source"]["fundamentals"]["snapshot_id"]) for row in rows]
        return {"tool": "get_revision_history", "world": world, "data": {"revisions": revisions}, "evidence": revisions, "quality": {"status": "VERIFIED"}, "release": {"serving_release_id": manifest["serving_release_id"], "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_sha256": TEMPORAL_CONTRACT_SHA256}}

    async def _evidence_v2(self, evidence_id: str, world: dict, rid: str) -> dict:
        _, manifest = await load_release(self.env)
        index = await artifact(self.env, manifest, "identity/resolver_index.json")
        for entry in index.get("symbols", {}).values():
            path = entry.get("artifact_path")
            if not path: continue
            for source in ("annual", "quarterly"):
                try: rows = await artifact(self.env, manifest, f"{path}/fundamentals/{source}.json")
                except ServingV2Error as error:
                    if error.code == "SERVING_ARTIFACT_NOT_FOUND": continue
                    raise
                matches = [row for row in rows if row.get("evidence_id") == evidence_id]
                if matches:
                    return {"tool": "get_evidence", "world": world, "data": {"evidence": matches}, "evidence": matches, "quality": {"status": "VERIFIED"}, "release": {"serving_release_id": manifest["serving_release_id"], "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_sha256": TEMPORAL_CONTRACT_SHA256}}
        raise ContractError("ENTITY_NOT_FOUND", "evidence was not found", status=404)

    async def _screen_v2(self, args: dict, world: dict, rid: str) -> dict:
        # Latest screening gets a short, hard-bounded pointer cache so cold
        # unique screens do not pay the uncached CURRENT tail repeatedly.
        # The normal financial tools retain their configured CURRENT semantics.
        _, manifest = await load_release(self.env, pointer_ttl_ms=5000)
        cache_key = json.dumps({"release": manifest["serving_release_id"], "world": world, "args": args}, sort_keys=True, separators=(",", ":"))
        cached = _SCREEN_CACHE.get(cache_key)
        if cached is not None:
            return cached
        cache_url = "https://screen-v2.internal/" + hashlib.sha256(cache_key.encode()).hexdigest()
        try:
            from js import Request, caches  # type: ignore
            cache_response = await caches.default.match(Request.new(cache_url))
            if cache_response is not None:
                cached = json.loads(await cache_response.text())
                _SCREEN_CACHE[cache_key] = cached
                return cached
        except Exception:
            pass
        if args.get("as_of") is not None:
            raise ContractError("PERIOD_NOT_AVAILABLE", "historical screens are not materialized in this release", status=422)
        screen = await artifact(self.env, manifest, "screens/latest.json")
        filters = args.get("filters", [])
        fields_requested = {item.get("field") for item in filters}
        fields_requested.update(item.get("field") for item in args.get("sort", []))
        supported = set(screen.get("fields", [])) & {"revenue", "operating_margin", "net_income", "last_price"}
        for field in fields_requested:
            if field not in supported:
                raise ContractError("METRIC_NOT_AVAILABLE", f"screen field is not materialized: {field}", status=422)

        evaluated = []
        for record in screen.get("rows", [])[:100]:
            values = record.get("values", {})
            fields = {field: (values.get(field) or {}).get("value") for field in fields_requested}
            evaluated.append((record.get("symbol"), fields, record.get("evidence_ids", [])))
        rows = []
        evidence_rows = []
        for symbol, fields, row_evidence in evaluated:
            def matches(item):
                value = fields.get(item.get("field")); op = item.get("operator")
                if value is None: return False
                try:
                    left, right = Decimal(str(value)), item.get("value")
                    if op == "between": return left >= Decimal(str(item["values"][0])) and left <= Decimal(str(item["values"][1]))
                    right = Decimal(str(right))
                    return {"eq": left == right, "ne": left != right, "gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[op]
                except (KeyError, ValueError, InvalidOperation): return False
            if all(matches(item) for item in filters):
                rows.append({"symbol": symbol, "values": fields, "evidence": row_evidence})
                evidence_rows.extend(row_evidence)
        for item in reversed(args.get("sort", [])):
            rows.sort(key=lambda row: (row["values"].get(item["field"]) is None, row["values"].get(item["field"])), reverse=item.get("direction", "desc") == "desc")
        result = {"tool": "screen", "world": world, "data": {"results": rows[:args.get("limit", 100)]}, "evidence": evidence_rows[:args.get("limit", 100) * 4], "quality": {"status": "VERIFIED"}, "release": {"serving_release_id": manifest["serving_release_id"], "temporal_schema_version": TEMPORAL_SCHEMA_VERSION, "temporal_contract_sha256": TEMPORAL_CONTRACT_SHA256}}
        if len(_SCREEN_CACHE) >= 16:
            _SCREEN_CACHE.pop(next(iter(_SCREEN_CACHE)))
        _SCREEN_CACHE[cache_key] = result
        try:
            from js import Request, Response, caches  # type: ignore
            cache_response = Response.new(json.dumps(result, separators=(",", ":")))
            cache_response.headers.set("Cache-Control", "public, max-age=300")
            await caches.default.put(Request.new(cache_url), cache_response)
        except Exception:
            pass
        return result

    async def _mcp(self, request, rid: str):
        body = await request.json()
        is_batch = isinstance(body, list)
        if is_batch:
            body = body[0] if body and isinstance(body[0], dict) else {}
        method = body.get("method") if isinstance(body, dict) else None
        rpc_id = body.get("id") if isinstance(body, dict) else None
        params = body.get("params") if isinstance(body, dict) and isinstance(body.get("params"), dict) else {}
        protocol_header = request.headers.get("MCP-Protocol-Version")
        stateless = protocol_header == "2026-07-28"
        try:
            if protocol_header and protocol_header not in {"2025-06-18", "2025-11-25", "2026-07-28"}:
                return self._mcp_error(rpc_id, -32004, "Unsupported protocol version", {"supported": ["2025-11-25", "2026-07-28"], "requested": protocol_header}, 400)
            if stateless:
                meta = params.get("_meta")
                expected_meta = {
                    "io.modelcontextprotocol/protocolVersion",
                    "io.modelcontextprotocol/clientCapabilities",
                }
                if not isinstance(meta, dict) or not expected_meta.issubset(meta):
                    return self._mcp_error(rpc_id, -32602, "Invalid params: required stateless _meta is missing", None, 400)
                if meta.get("io.modelcontextprotocol/protocolVersion") != protocol_header:
                    return self._mcp_error(rpc_id, -32020, "Header/body protocol version mismatch", None, 400)
                if not isinstance(meta.get("io.modelcontextprotocol/clientCapabilities"), dict):
                    return self._mcp_error(rpc_id, -32602, "Invalid params: clientCapabilities must be an object", None, 400)
                if request.headers.get("Mcp-Method") != method:
                    return self._mcp_error(rpc_id, -32020, "Mcp-Method header does not match request method", None, 400)
                header_name = request.headers.get("Mcp-Name")
                body_name = params.get("name") if method in {"tools/call", "prompts/get"} else params.get("uri") if method == "resources/read" else None
                if body_name is not None and header_name != body_name:
                    return self._mcp_error(rpc_id, -32020, "Mcp-Name header does not match request identity", None, 400)
                if body_name is None and header_name is not None:
                    return self._mcp_error(rpc_id, -32020, "Mcp-Name is not valid for this method", None, 400)
            if stateless and method in {"initialize", "notifications/initialized", "ping", "logging/setLevel", "completion/complete"}:
                return self._mcp_error(rpc_id, -32601, "method not found", None, 404)
            if stateless and method == "server/discover":
                return self._response({"jsonrpc": "2.0", "id": rpc_id, "result": {"supportedVersions": ["2025-11-25", "2026-07-28"], "capabilities": {"tools": {"listChanged": False}}, "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "zion-tool-contract-v2", "version": CONTRACT_VERSION}}}})
            if method == "initialize":
                requested = (body.get("params") or {}).get("protocolVersion")
                protocol_version = requested if requested in {"2025-06-18", "2025-11-25"} else "2025-11-25"
                return self._response({"jsonrpc": "2.0", "id": rpc_id, "result": {"protocolVersion": protocol_version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "zion-tool-contract-v2", "version": CONTRACT_VERSION}}})
            if method == "notifications/initialized":
                return Response("", status=202, headers={"content-type": "application/json"})
            if method == "ping":
                return self._response({"jsonrpc": "2.0", "id": rpc_id, "result": {}})
            if method == "logging/setLevel":
                return self._response({"jsonrpc": "2.0", "id": rpc_id, "result": {}})
            if method == "completion/complete":
                return self._response({"jsonrpc": "2.0", "id": rpc_id, "result": {"completion": {"values": [], "total": 0, "hasMore": False}}})
            if method == "tools/list":
                result = {"tools": [{"name": name, "description": definition["description"], "inputSchema": definition["input_schema"], "annotations": {"readOnlyHint": True, "destructiveHint": False}} for name, definition in TOOLS.items()]}
                if stateless:
                    result.update({"ttlMs": 300000, "cacheScope": "public"})
                return self._response({"jsonrpc": "2.0", "id": rpc_id, "result": result})
            if method == "tools/call":
                params = body.get("params") or {}; name = params.get("name"); arguments = dict(params.get("arguments") or {})
                call_world = arguments.pop("world", {"world_type": "real", "world_id": "us-public-markets"})
                result = await self._tool_v2(name, {"world": call_world, "arguments": arguments}, rid)
                return self._response({"jsonrpc": "2.0", "id": rpc_id, "result": {"content": [{"type": "json", "json": result}], "structuredContent": result}})
            if rpc_id is None:
                return Response("", status=202, headers={"content-type": "application/json"})
            return self._response({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found"}}, 404 if stateless else 400)
        except ContractError as error:
            return self._response({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": error.code, "data": {"request_id": rid, "retryable": error.retryable}}}, 400 if stateless else 200)
        except LookupError:
            return self._response({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32004, "message": "resource not found", "data": {"request_id": rid}}}, 200)

    def _mcp_error(self, rpc_id, code: int, message: str, data: dict | None = None, status: int = 200):
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return self._response({"jsonrpc": "2.0", "id": rpc_id, "error": error}, status)

    def _response(self, value: dict, status: int = 200):
        telemetry = finish_telemetry()
        headers = {"content-type": "application/json", "x-serving-telemetry": json.dumps(telemetry, separators=(",", ":"))}
        return Response(json.dumps(value, separators=(",", ":")), status=status, headers=headers)

    def _error(self, rid: str, code: str, message: str, status: int, retryable: bool = False):
        return self._response({"error": {"request_id": rid, "code": code, "message": message, "retryable": retryable}}, status)
