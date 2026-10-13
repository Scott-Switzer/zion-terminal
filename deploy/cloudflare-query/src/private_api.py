"""Bounded private queries over one integrity-checked, immutable release."""

import re

from private_financials import (
    PrivateFinancialsError,
    annual_revenue_growth,
    normalize_observation,
)
from serving_v2 import (
    ServingV2Error,
    _as_of,
    _available,
    _identity_covers,
    artifact,
    load_release,
    resolve_security,
)
from temporal_core import normalize_source_instant

OPERATIONS = ("identity", "financials", "revenue_growth")


def invalid(message):
    raise PrivateFinancialsError("INVALID_REQUEST", message, 400)


def validate_request(request: dict) -> dict:
    if not isinstance(request, dict):
        invalid("a JSON object is required")
    operation = request.get("operation")
    if operation not in OPERATIONS:
        invalid("unsupported operation")
    allowed = {"operation", "symbol", "as_of", "expected_release_id"}
    allowed |= {"metrics", "period", "lookback"} if operation == "financials" else set()
    allowed |= {"years"} if operation == "revenue_growth" else set()
    if set(request) - allowed:
        invalid("unknown argument")
    symbol = request.get("symbol")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{0,15}", symbol):
        invalid("a bounded company symbol is required")
    if "expected_release_id" in request:
        release = request["expected_release_id"]
        if not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", release):
            invalid("invalid expected release")
    try:
        _as_of(request.get("as_of"))
    except Exception:
        invalid("invalid as_of date")
    if "as_of" in request and not isinstance(request["as_of"], str):
        invalid("as_of must be a date or instant string")
    if operation == "financials":
        metrics = request.get("metrics", ["revenue"])
        if (
            not isinstance(metrics, list)
            or not 1 <= len(metrics) <= 20
            or any(
                not isinstance(m, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", m)
                for m in metrics
            )
            or len(set(metrics)) != len(metrics)
        ):
            invalid("metrics must contain 1 to 20 distinct metric names")
        if request.get("period", "annual") not in ("annual", "quarterly"):
            invalid("period must be annual or quarterly")
        n = request.get("lookback", 40)
        if type(n) is not int or not 1 <= n <= 40:
            invalid("lookback must be an integer from 1 to 40")
    if operation == "revenue_growth":
        n = request.get("years", 5)
        if type(n) is not int or not 1 <= n <= 10:
            invalid("years must be an integer from 1 to 10")
    return {**request, "symbol": symbol.upper()}


async def query_existing(env, request: dict, *, request_id: str) -> dict:
    request = validate_request(request)
    try:
        return await _query(env, request, request_id)
    except PrivateFinancialsError:
        raise
    except ServingV2Error as error:
        if error.code == "SERVING_ARTIFACT_NOT_FOUND":
            raise PrivateFinancialsError(
                "DATA_NOT_AVAILABLE", "requested data is not published", 404
            ) from None
        raise PrivateFinancialsError(
            "INTEGRITY_OR_STORAGE_FAILURE", "stored data could not be verified", 503
        ) from None
    except Exception:
        raise PrivateFinancialsError(
            "DATA_UNAVAILABLE", "stored data is unavailable", 503
        ) from None


async def _query(env, request: dict, request_id: str) -> dict:
    current, manifest = await load_release(env)
    if not re.fullmatch(r"[0-9a-f]{64}", str(current.get("manifest_sha256", ""))):
        raise PrivateFinancialsError(
            "INTEGRITY_OR_STORAGE_FAILURE", "release digest is required", 503
        )
    release_id = manifest["serving_release_id"]
    if request.get("expected_release_id", release_id) != release_id:
        raise PrivateFinancialsError("RELEASE_MISMATCH", "expected release is not current", 409)
    if request.get("as_of"):
        try:
            index = await artifact(env, manifest, "identity/resolver_index.json")
        except ServingV2Error as error:
            if error.code != "SERVING_ARTIFACT_NOT_FOUND":
                raise
            raise PrivateFinancialsError(
                "ENTITY_NOT_AVAILABLE_AT_DATE", "dated identity is not published", 422
            ) from None
        entry = index.get("symbols", {}).get(request["symbol"])
        if (
            not entry
            or not entry.get("valid_from")
            or not _identity_covers(entry, request["as_of"])
        ):
            raise PrivateFinancialsError(
                "ENTITY_NOT_AVAILABLE_AT_DATE",
                "company identity is not valid at the requested date",
                422,
            )
    resolved = await resolve_security(env, manifest, request["symbol"], request.get("as_of"))
    identity = resolved["identity"]
    key = identity["artifact_path"]
    try:
        snapshot = await artifact(env, manifest, f"{key}/snapshot.json")
    except ServingV2Error as error:
        if error.code == "SERVING_ARTIFACT_NOT_FOUND":
            raise PrivateFinancialsError(
                "ENTITY_NOT_FOUND", "company is not in this release", 404
            ) from None
        raise
    entity = {k: snapshot.get(k) for k in ("entity_id", "symbol", "cik")}
    if not entity["entity_id"]:
        raise PrivateFinancialsError("DATA_UNAVAILABLE", "issuer identity is missing", 503)
    operation = request["operation"]
    missing, observations = [], []
    data = {"identity": entity}
    if operation != "identity":
        period = request.get("period", "annual")
        rows = await artifact(env, manifest, f"{key}/fundamentals/{period}.json")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise PrivateFinancialsError("DATA_UNAVAILABLE", "invalid financial observations", 503)
        metrics = request.get("metrics", ["revenue"])
        eligible = [r for r in rows if r.get("metric_id") in metrics]
        cutoff = _as_of(request.get("as_of"))
        revisions, latest = {}, {}
        for row in eligible:
            if not _available(row, cutoff):
                continue
            period_key = tuple(
                row.get(k) for k in ("metric_id", "period_type", "period_start", "period_end")
            )
            available = row.get("available_at")
            instant = (
                normalize_source_instant(available, field="available_at").epoch_ns
                if available
                else None
            )
            revision_key = (*period_key, instant)
            value_key = tuple(
                row.get(k)
                for k in ("value_decimal", "value", "unit", "accounting_basis", "entity_id")
            )
            if revision_key in revisions and revisions[revision_key] != value_key:
                raise PrivateFinancialsError(
                    "AMBIGUOUS_OBSERVATION", "conflicting observations require resolution"
                )
            revisions[revision_key] = value_key
            if period_key in latest:
                previous_instant, _ = latest[period_key]
                if instant is None or previous_instant is None:
                    raise PrivateFinancialsError(
                        "AMBIGUOUS_OBSERVATION", "revision availability is not established"
                    )
                if instant <= previous_instant:
                    continue
            latest[period_key] = (instant, row)
        selected = sorted(
            (row for _, row in latest.values()),
            key=lambda row: (row.get("period_end") or "", row.get("metric_id") or ""),
        )
        limit = request.get("lookback", 40)
        for metric in metrics:
            native = [r for r in selected if r.get("metric_id") == metric]
            if not native:
                missing.append(f"METRIC_NOT_AVAILABLE:{metric}")
            for row in native[-limit:]:
                if row.get("entity_id") != entity["entity_id"] or row.get("period_type") != period:
                    raise PrivateFinancialsError(
                        "DATA_UNAVAILABLE", "financial identity or period mismatch", 503
                    )
                observations.append(normalize_observation(row, release_id=release_id))
        data = {"observations": observations}
        if operation == "revenue_growth":
            data = annual_revenue_growth(observations, years=request.get("years", 5))
    return {
        "schema_version": "zion-private-query-v1",
        "request_id": request_id,
        "operation": operation,
        "entity": entity,
        "data": data,
        "coverage": {
            "scope": "stored_us_public_company_observations",
            "status": "PARTIAL" if missing else "AVAILABLE",
            "missing_reasons": missing,
            "as_of": request.get("as_of"),
            "independent_validation": "NOT_ESTABLISHED",
        },
        "evidence": [
            {
                "evidence_id": r["evidence_id"],
                "source_record_id": r["source_record_id"],
                "source_revision_id": r["source_revision_id"],
            }
            for r in observations
        ],
        "release": {
            "serving_release_id": release_id,
            "manifest_sha256": current["manifest_sha256"],
        },
    }
