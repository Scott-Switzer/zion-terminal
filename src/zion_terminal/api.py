from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .truth_query import METRICS, QueryServiceError, build_registry, git_sha
from .world_router import WorldRegistry, WorldRouteError


def create_app(*, registry: WorldRegistry | None = None, real_file: Path | None = None, synthetic_dir: Path | None = None, qc_report: Path | None = None):
    app = FastAPI(title="Zion Financial Truth Query", version="1.0.0", docs_url=None, redoc_url=None)
    query_registry = registry or build_registry(real_file, synthetic_dir, qc_report)

    def request_id(request: Request) -> str:
        incoming = request.headers.get("x-request-id", "")
        return incoming if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", incoming) else str(uuid.uuid4())

    def error_payload(request: Request, error: QueryServiceError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={"error": {"request_id": request_id(request), "code": error.code, "message": error.message, "retryable": error.retryable}})

    @app.exception_handler(QueryServiceError)
    async def query_error_handler(request: Request, exc: QueryServiceError):
        return error_payload(request, exc)

    @app.exception_handler(WorldRouteError)
    async def route_error_handler(request: Request, exc: WorldRouteError):
        error = QueryServiceError("INVALID_UPSTREAM_RESPONSE", str(exc), status=502)
        return error_payload(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return error_payload(request, QueryServiceError("INVALID_REQUEST", "request does not match FinancialQueryRequestV1"))

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "service": "zion-terminal", "schema_version": "1"}

    @app.get("/readyz")
    async def readyz():
        return {"status": "ready", "service": "zion-terminal"}

    @app.get("/v1/capabilities")
    async def capabilities():
        return {"schema_version": "1", "service_version": "1.0.0", "git_sha": git_sha(), "worlds": ["real", "synthetic"], "metrics": list(METRICS)}

    @app.post("/v1/query")
    async def query(request: Request):
        request_id_value = request_id(request)
        try:
            body: Any = await request.json()
        except Exception as exc:
            raise QueryServiceError("INVALID_REQUEST", "request body must be valid JSON") from exc
        if not isinstance(body, dict) or set(body) - {"query", "world", "as_of"} or not isinstance(body.get("world"), dict):
            raise QueryServiceError("INVALID_REQUEST", "request must contain query and world")
        if len(json_bytes(body)) > 32_000:
            raise QueryServiceError("INVALID_REQUEST", "request exceeds 32KB")
        body["request_id"] = request_id_value
        result = query_registry.query(body)
        result["request_id"] = request_id_value
        return result

    return app


def json_bytes(value: object) -> bytes:
    import json
    return json.dumps(value, separators=(",", ":")).encode()
