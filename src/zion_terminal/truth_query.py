from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from zion_terminal.temporal import TemporalError, normalize_source_instant, parse_instant
from zion_terminal.world_router import WorldRegistry

METRICS = ("revenue", "operating_margin", "last_price")
_METRIC_ALIASES = {
    "revenue": "revenue",
    "sales": "revenue",
    "operating margin": "operating_margin",
    "operating_margin": "operating_margin",
    "margin": "operating_margin",
    "last price": "last_price",
    "last_price": "last_price",
    "price": "last_price",
}
_STOPWORDS = {"GIVE", "ME", "WHAT", "ARE", "IS", "THE", "FOR", "AND", "OF", "SHOW", "REVENUE", "SALES", "OPERATING", "MARGIN", "LAST", "PRICE", "EBITDA"}


class QueryServiceError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


class QueryPlan:
    def __init__(self, symbol: str, metrics: list[str]):
        self.symbol = symbol
        self.metrics = metrics


def parse_query(text: str) -> QueryPlan:
    if not isinstance(text, str) or not text.strip() or len(text) > 2_000:
        raise QueryServiceError("INVALID_REQUEST", "query must be a non-empty string under 2000 characters")
    upper = text.upper()
    metrics: list[str] = []
    for phrase, metric in sorted(_METRIC_ALIASES.items(), key=lambda item: -len(item[0])):
        if phrase.upper() in upper and metric not in metrics:
            metrics.append(metric)
    if not metrics:
        raise QueryServiceError("QUERY_NOT_SUPPORTED", "supported metrics are revenue, operating margin, and last price", status=422)
    tokens = [token.strip(".,?!:;()[]{}") for token in upper.split()]
    candidates = [token for token in tokens if re.fullmatch(r"[A-Z][A-Z0-9_-]{1,9}", token) and token not in _STOPWORDS]
    if not candidates:
        raise QueryServiceError("QUERY_NOT_SUPPORTED", "a security or synthetic company symbol is required", status=422)
    return QueryPlan(candidates[-1], metrics)


def _as_of(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise QueryServiceError("INVALID_REQUEST", "as_of must be an ISO-8601 string")
    try:
        return parse_instant(value, field="as_of").as_datetime
    except TemporalError as exc:
        raise QueryServiceError("INVALID_REQUEST", str(exc)) from exc


def _evidence_response(world: dict[str, Any], entity: dict[str, Any], evidence: list[dict[str, Any]], release: dict[str, Any], request_id: str) -> dict[str, Any]:
    by_metric = {row["metric"]: row for row in evidence}
    metrics = [{"metric": row["metric"], "value": row["value"], "unit": row["unit"], "period": row["period"]} for row in evidence]
    clauses: list[str] = []
    if "revenue" in by_metric:
        clauses.append(f"{entity['symbol']} revenue was ${by_metric['revenue']['value'] / 1_000_000_000:.3f}B")
    if "operating_margin" in by_metric:
        clauses.append(f"operating margin was {by_metric['operating_margin']['value']:.2%}")
    if "last_price" in by_metric:
        clauses.append(f"the latest published price was ${by_metric['last_price']['value']:.2f}")
    return {"schema_version": "1", "request_id": request_id, "world": world, "entity": entity, "answer": "; ".join(clauses) + ".", "metrics": metrics, "calculations": [row["calculation"] for row in evidence if row.get("calculation")], "evidence": evidence, "quality": {"status": "VERIFIED", "llm_required": False, "source_count": len(evidence), "qc_status": release.get("qc_status")}, "release": release}


class RealReleaseClient:
    """Client for a pre-materialized, PPE-derived serialized query release."""

    def __init__(self, release_file: Path):
        self.release_file = release_file

    def _snapshot(self) -> dict[str, Any]:
        try:
            return json.loads(self.release_file.read_text())
        except FileNotFoundError as exc:
            raise QueryServiceError("RELEASE_NOT_AVAILABLE", "PPE published release is unavailable", retryable=True, status=503) from exc
        except json.JSONDecodeError as exc:
            raise QueryServiceError("INVALID_UPSTREAM_RESPONSE", "PPE release is not valid JSON", status=502) from exc

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot()  # pin CURRENT/release exactly once per request
        plan = parse_query(request["query"])
        if request["world"].get("world_id") != snapshot["world"].get("world_id"):
            raise QueryServiceError("WORLD_NOT_FOUND", "real world is not available", status=404)
        if plan.symbol != snapshot["entity"]["symbol"]:
            raise QueryServiceError("ENTITY_NOT_FOUND", f"{plan.symbol} is not in the published real release", status=404)
        as_of = _as_of(request.get("as_of"))
        evidence = _filter_evidence(snapshot["evidence"], plan.metrics, as_of)
        if len(evidence) < len(plan.metrics):
            raise QueryServiceError(
                "METRIC_NOT_AVAILABLE",
                "one or more requested metrics are unavailable at as_of",
                status=404,
            )
        return _evidence_response(
            snapshot["world"],
            snapshot["entity"],
            evidence,
            snapshot.get("release", {}),
            request["request_id"],
        )


class SyntheticReleaseClient:
    """Client restricted to a certified native release's public artifact set."""

    def __init__(self, release_dir: Path, qc_report: Path | None = None, *, allow_uncertified: bool = False):
        self.release_dir = release_dir.resolve()
        self.qc_report = qc_report.resolve() if qc_report else self.release_dir / "qc_report.json"
        self.allow_uncertified = allow_uncertified

    def _inside_public(self, relative: str) -> Path:
        path = (self.release_dir / relative).resolve()
        public_root = (self.release_dir / "public").resolve()
        if public_root not in path.parents or path.is_symlink():
            raise QueryServiceError("INVALID_UPSTREAM_RESPONSE", "synthetic public artifact path rejected", status=502)
        return path

    def _snapshot(self) -> dict[str, Any]:
        try:
            manifest = json.loads((self.release_dir / "manifest.json").read_text())
            certification = json.loads(self.qc_report.read_text())
        except FileNotFoundError as exc:
            raise QueryServiceError("RELEASE_NOT_AVAILABLE", "synthetic release or QC report is unavailable", retryable=True, status=503) from exc
        if not self.allow_uncertified and certification.get("status") != "PASS":
            raise QueryServiceError("WORLD_NOT_CERTIFIED", "synthetic world does not have a PASS QC certification", status=503)
        world = manifest.get("world", {})
        entities = json.loads(self._inside_public("public/entities.json").read_text())["entities"]
        financials = json.loads(self._inside_public("public/financials.json").read_text())["observations"]
        prices = json.loads(self._inside_public("public/prices.json").read_text())["prices"]
        return {"manifest": manifest, "certification": certification, "world": world, "entities": entities, "financials": financials, "prices": prices}

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot()
        plan = parse_query(request["query"])
        world = snapshot["world"]
        if request["world"].get("world_id") != world.get("world_id"):
            raise QueryServiceError("WORLD_NOT_FOUND", "synthetic world is not available", status=404)
        entity = next((item for item in snapshot["entities"] if item["symbol"].upper() == plan.symbol), None)
        if entity is None:
            raise QueryServiceError("ENTITY_NOT_FOUND", f"{plan.symbol} is not in the synthetic world", status=404)
        as_of = _as_of(request.get("as_of"))
        evidence = [row for row in snapshot["financials"] if row["entity_id"] == entity["entity_id"] and row["metric"] in plan.metrics]
        prices = [row for row in snapshot["prices"] if row["security"] == plan.symbol]
        if "last_price" in plan.metrics and prices:
            row = prices[-1]
            evidence.append({"entity_id": entity["entity_id"], "metric": "last_price", "value": row["close"], "unit": row.get("unit", "USD/share"), "period": row["session"], "observation_at": row["observation_at"], "available_at": row["available_at"], "retrieved_at": row["available_at"], "source": "market-fuzzer-native", "source_record": row["provenance"]["source_record"], "calculation": None, "world": world, "provenance": {"producer": snapshot["manifest"]["producer"], "artifact": "public/prices.json", "artifact_sha256": snapshot["manifest"]["artifact_hashes"]["public/prices.json"]["sha256"]}, "quality": {"status": "observed"}})
        evidence = _filter_evidence(evidence, plan.metrics, as_of)
        if len(evidence) < len(plan.metrics):
            raise QueryServiceError("METRIC_NOT_AVAILABLE", "one or more requested metrics are unavailable at as_of", status=404)
        release = {"world_version": world.get("version"), "producer_sha": snapshot["manifest"].get("producer", {}).get("git_sha"), "qc_status": snapshot["certification"].get("status"), "qc_report_sha256": hashlib.sha256(self.qc_report.read_bytes()).hexdigest()}
        return _evidence_response(world, {key: entity[key] for key in ("entity_id", "display_name", "symbol")}, evidence, release, request["request_id"])


def _source_as_of(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return normalize_source_instant(value, field="available_at").as_datetime
    except TemporalError:
        return None


def _filter_evidence(evidence: list[dict[str, Any]], metrics: list[str], as_of: datetime | None) -> list[dict[str, Any]]:
    output = []
    for metric in metrics:
        candidates = [row for row in evidence if row.get("metric") == metric]
        if as_of is not None:
            candidates = [row for row in candidates if _source_as_of(row.get("available_at")) is not None and _source_as_of(row["available_at"]) <= as_of]
        if candidates:
            output.append(candidates[-1])
    return output


def build_registry(real_file: Path | None, synthetic_dir: Path | None, qc_report: Path | None) -> WorldRegistry:
    registry = WorldRegistry()
    if real_file:
        client = RealReleaseClient(real_file)
        registry.register("real", client.handle)
    if synthetic_dir:
        client = SyntheticReleaseClient(synthetic_dir, qc_report)
        registry.register("synthetic", client.handle)
    return registry


def git_sha() -> str:
    configured = os.getenv("ZION_GIT_SHA")
    if configured:
        return configured
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"
