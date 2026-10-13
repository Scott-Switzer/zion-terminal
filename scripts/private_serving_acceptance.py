"""Read-only acceptance; tokens and financial payloads never enter the report."""

import argparse
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

COVERAGE_CODES = {
    "COMPARABILITY_NOT_ESTABLISHED",
    "INCOMPLETE_HISTORY",
    "INCOMPARABLE_OBSERVATIONS",
    "NON_POSITIVE_REVENUE",
    "ENTITY_NOT_FOUND",
    "ENTITY_NOT_AVAILABLE_AT_DATE",
    "DATA_NOT_AVAILABLE",
    "AMBIGUOUS_PERIOD",
    "AMBIGUOUS_OBSERVATION",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_http(base, method, path, payload, token):
    headers = {"content-type": "application/json", "accept": "application/json"}
    if token:
        headers["authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("response too large")
        return response.code, json.loads(raw)


def run_acceptance(base_url, token, *, request_fn=request_http):
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("a credential-free HTTPS origin is required")
    if not token:
        raise ValueError("runtime token is required")
    base = base_url.rstrip("/")
    checks = []

    def execute(name, method, path, payload=None, auth=token):
        started = time.perf_counter()
        try:
            status, body = request_fn(base, method, path, payload, auth)
            if not isinstance(body, dict):
                raise ValueError("invalid response")
        except Exception:
            status, body = 0, {}
        check = {
            "name": name,
            "http_status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "status": "FAIL",
        }
        checks.append(check)
        return check, body

    a, _ = execute("missing_auth", "GET", "/private/v1/capabilities", auth="")
    a["status"] = "PASS" if a["http_status"] == 401 else "FAIL"
    b, _ = execute(
        "invalid_auth", "GET", "/private/v1/capabilities", auth=secrets.token_urlsafe(32)
    )
    b["status"] = "PASS" if b["http_status"] == 401 else "FAIL"
    c, caps = execute("capabilities", "GET", "/private/v1/capabilities")
    c["status"] = (
        "PASS"
        if c["http_status"] == 200
        and caps.get("operations") == ["identity", "financials", "revenue_growth"]
        else "FAIL"
    )
    authenticated = all(check["status"] == "PASS" for check in (a, b, c))
    for symbol in ("AAPL", "MSFT", "NVDA"):
        release = None
        for operation in ("identity", "financials", "revenue_growth"):
            payload = {"symbol": symbol, "operation": operation}
            if release:
                payload["expected_release_id"] = release
            check, body = execute(f"{symbol}:{operation}", "POST", "/private/v1/query", payload)
            error_body = body.get("error")
            code = error_body.get("code") if isinstance(error_body, dict) else None
            if check["http_status"] in (404, 422) and code in COVERAGE_CODES:
                check.update(status="PARTIAL", coverage_reason=code)
                continue
            if check["http_status"] != 200 or body.get("schema_version") != "zion-private-query-v1":
                continue
            actual = body.get("release", {}).get("serving_release_id")
            if not isinstance(actual, str) or not actual or (release and actual != release):
                continue
            # Record no raw upstream fields, values, IDs, messages or response headers.
            release = actual
            data, coverage = body.get("data", {}), body.get("coverage", {})
            if not isinstance(data, dict) or not isinstance(coverage, dict):
                continue
            if operation == "financials" and (
                not data.get("observations") or coverage.get("missing_reasons")
            ):
                check.update(status="PARTIAL", coverage_reason="MISSING_FINANCIAL_OBSERVATIONS")
            elif operation == "revenue_growth" and len(data.get("year_over_year", [])) != 5:
                check.update(status="PARTIAL", coverage_reason="INCOMPLETE_HISTORY")
            else:
                check["status"] = "PASS"
    status = (
        "FAIL"
        if any(c["status"] == "FAIL" for c in checks)
        else "PARTIAL"
        if any(c["status"] == "PARTIAL" for c in checks)
        else "PASS"
    )
    return {
        "schema_version": "zion-private-acceptance-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "authenticated_api_verified": authenticated,
        "checks": checks,
        "limitations": [
            "transport and coverage acceptance, not independent source reconciliation",
            "small sequential sample, not a latency percentile or load test",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = run_acceptance(
            os.environ.get("ZION_PRIVATE_BASE_URL", ""),
            os.environ.get("ZION_PRIVATE_API_TOKEN", ""),
        )
    except Exception:
        print("Acceptance configuration invalid; supply HTTPS origin and runtime token.")
        return 2
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"])}))
    return {"PASS": 0, "PARTIAL": 1, "FAIL": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
