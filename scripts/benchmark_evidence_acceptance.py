"""Read-only acceptance for the deployed DDFI benchmark publication-evidence gate.

Checks, against an authenticated private benchmark origin:

* anonymous and invalid-bearer access stays denied;
* historical selection binds the served release to the reviewed evidence catalog
  and to the source bytes the manifest names;
* selection before the release's publication date fails closed, with and
  without an explicit release pin;
* current selection keeps its existing contract (no evidence fields);
* every evidenced dataset resolves at an eligible cutoff.

Tokens never enter the report, and only release/provenance identity is recorded
— never observation values or source bytes.
"""

import argparse
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

CATALOG_SHA256 = "d8c75248feff3e39ae5654a2b3cedc7f847b36ea551b6ff207ba3f3b3c60414f"
EVIDENCE_STATE = "REVIEWED_CATALOG_BOUND_TO_SERVED_SOURCE_BYTES"
CURRENT_EVIDENCE_STATE = "NOT_APPLICABLE_CURRENT_SELECTION"
PUBLICATION_METHODS = {"SERVER_LAST_MODIFIED", "PUBLISHED_PAGE"}

# dataset_id -> (release_id, published_at, primary measure). These are the
# identities the canonical index publishes; a serving regression that selects a
# different vintage fails acceptance rather than being reported as a pass.
EXPECTED = {
    "capex_reinvestment": (
        "bcd291a646f52393ae5e6b539cfd3936e08aba566f7e34fc39448d81ca7f816c",
        "2026-01-08",
        "net_capex_to_sales",
        "Advertising",
    ),
    "cost_of_capital": (
        "11c1e6bb76d9db12feeec3e3949a9ffe370d0ace87f3047aaac66f431270ccc9",
        "2026-01-12",
        "wacc",
        "Advertising",
    ),
    "country_risk_premiums": (
        "a31958cc7691a0521eac5c6dd45d15ac74d38b995ea307f60c5f915aa20c8735",
        "2026-07-10",
        "equity_risk_premium",
        "Abu Dhabi",
    ),
    "ev_ebitda": (
        "8a3e71a63c00ef4bafc88b60358322ab57913963637fb23600059ab074ac6ed2",
        "2026-01-08",
        "ev_to_ebitda__all_firms",
        "Advertising",
    ),
    "fundamental_growth": (
        "fef0454724bdf68fd2af915f9df28c84b8e5c04589508b8697f8e576a929cb74",
        "2026-01-08",
        "expected_growth_fundamental",
        "Advertising",
    ),
    "historical_implied_erp": (
        "3e115f6889f92c7539a3a0d737a1280f72f0ecefcb027321b902c9fa3080a473",
        "2026-01-08",
        "implied_erp_fcfe",
        "1961",
    ),
    "industry_aggregates": (
        "2fabaea6bbf32ce768e4ff7ec3d77323aa8c4d6656afc4d5a47a065f78afcc9d",
        "2026-01-08",
        "operating_margin",
        "Advertising",
    ),
    "industry_betas": (
        "1a0441bae4161d8482cb1c54e0f324c839f3d1da2e7d6bbd3b7fa5807350c2cc",
        "2026-04-12",
        "unlevered_beta",
        "Advertising",
    ),
}
ELIGIBLE_CUTOFF = "2026-10-01"
COST_OF_CAPITAL_CUTOFF = "2026-06-01"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_http(base, method, path, payload, token):
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": "zion-benchmark-evidence-acceptance/1",
    }
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


def _loopback(hostname):
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _code(body):
    error = body.get("error") if isinstance(body, dict) else None
    return error.get("code") if isinstance(error, dict) else None


def evidence_provenance(body, *, dataset_id):
    """Assert the historical release/provenance contract; return a sanitized view."""
    release_id, published_at, _measure, _subject = EXPECTED[dataset_id]
    release = body.get("release")
    coverage = body.get("coverage")
    if not isinstance(release, dict) or not isinstance(coverage, dict):
        return None, "missing release or coverage block"
    if release.get("ddfi_release_id") != release_id or release.get("published_at") != published_at:
        return None, "selected release does not match the published identity"
    if release.get("dataset_id") != dataset_id:
        return None, "selected dataset mismatch"
    if coverage.get("publication_evidence") != EVIDENCE_STATE:
        return None, "historical response is not evidence-gated"
    provenance = release.get("publication_evidence")
    if not isinstance(provenance, dict):
        return None, "evidence provenance is missing"
    if provenance.get("catalog_sha256") != CATALOG_SHA256:
        return None, "evidence catalog digest mismatch"
    if provenance.get("method") not in PUBLICATION_METHODS:
        return None, "publication method is not reviewed"
    source_sha = release.get("source_sha256")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
        return None, "served source digest is missing"
    if coverage.get("source_sha256") != source_sha:
        return None, "coverage and release source digests disagree"
    observed = provenance.get("observed_at")
    if not isinstance(observed, str) or not observed:
        return None, "evidence observation time is missing"
    if provenance.get("download_precision") not in {"day", "instant"}:
        return None, "retrieval precision is not disclosed"
    return {
        "release_id": release_id,
        "published_at": published_at,
        "selection_mode": release.get("selection_mode"),
        "source_sha256": source_sha,
        "catalog_sha256": provenance["catalog_sha256"],
        "method": provenance["method"],
        "download_precision": provenance["download_precision"],
    }, None


def run_acceptance(base_url, token, *, request_fn=request_http):
    parsed = urlparse(base_url)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or (parsed.scheme == "http" and not _loopback(parsed.hostname))
    ):
        raise ValueError("a credential-free origin is required (http only on loopback)")
    if not token:
        raise ValueError("runtime token is required")
    base = base_url.rstrip("/")
    checks = []

    def execute(name, payload, auth=token):
        started = time.perf_counter()
        try:
            status, body = request_fn(base, "POST", "/private/v1/query", payload, auth)
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
        if status != 200:
            code = _code(body)
            if code:
                check["error_code"] = code
        checks.append(check)
        return check, body

    denied, _ = execute("anonymous_denied", {"operation": "benchmark_context"}, auth="")
    denied["status"] = "PASS" if denied["http_status"] == 401 else "FAIL"
    bogus, _ = execute(
        "invalid_bearer_denied", {"operation": "benchmark_context"}, auth=secrets.token_urlsafe(32)
    )
    bogus["status"] = "PASS" if bogus["http_status"] == 401 else "FAIL"

    # Subjects are pinned with the release identity: a release that no longer
    # carries them is a different catalog than the one this script accepted.
    subjects = {dataset_id: spec[3] for dataset_id, spec in EXPECTED.items()}

    historical = {}
    if "cost_of_capital" in subjects:
        check, body = execute(
            "historical:cost_of_capital:2026-06-01",
            {
                "operation": "benchmark_context",
                "dataset_id": "cost_of_capital",
                "subject": subjects["cost_of_capital"],
                "measures": [EXPECTED["cost_of_capital"][2]],
                "as_of": COST_OF_CAPITAL_CUTOFF,
            },
        )
        provenance, reason = evidence_provenance(body, dataset_id="cost_of_capital") if body else (None, "no response")
        coverage = body.get("coverage") or {}
        observations = (body.get("data") or {}).get("observations") or []
        served = [o for o in observations if isinstance(o, dict) and o.get("value_decimal") is not None]
        if (
            check["http_status"] == 200
            and provenance is not None
            and provenance["selection_mode"] == "HISTORICAL_AS_OF"
            and coverage.get("as_of") == COST_OF_CAPITAL_CUTOFF
            and served
        ):
            check["status"] = "PASS"
            check["provenance"] = provenance
            check["served_observation"] = True
            historical["cost_of_capital"] = provenance
        elif check["status"] != "PASS":
            check["reason"] = reason or "historical selection contract not satisfied"

        before, body = execute(
            "prepublication:dataset_cutoff",
            {
                "operation": "benchmark_context",
                "dataset_id": "cost_of_capital",
                "subject": subjects["cost_of_capital"],
                "measures": [EXPECTED["cost_of_capital"][2]],
                "as_of": "2026-01-11",
            },
        )
        before["status"] = (
            "PASS"
            if before["http_status"] == 404
            and before.get("error_code") == "HISTORICAL_COVERAGE_UNAVAILABLE"
            else "FAIL"
        )

        pinned, body = execute(
            "prepublication:explicit_release",
            {
                "operation": "benchmark_context",
                "release_id": EXPECTED["cost_of_capital"][0],
                "subject": subjects["cost_of_capital"],
                "measures": [EXPECTED["cost_of_capital"][2]],
                "as_of": "2026-01-11",
            },
        )
        pinned["status"] = (
            "PASS"
            if pinned["http_status"] == 404
            and pinned.get("error_code") == "BENCHMARK_VINTAGE_UNAVAILABLE"
            else "FAIL"
        )

        current, body = execute(
            "current:cost_of_capital",
            {
                "operation": "benchmark_context",
                "dataset_id": "cost_of_capital",
                "subject": subjects["cost_of_capital"],
                "measures": [EXPECTED["cost_of_capital"][2]],
            },
        )
        release = body.get("release") or {}
        coverage = body.get("coverage") or {}
        current_ok = (
            current["http_status"] == 200
            and release.get("ddfi_release_id") == EXPECTED["cost_of_capital"][0]
            and release.get("selection_mode") == "CURRENT_EXPLICIT"
            and "publication_evidence" not in release
            and coverage.get("publication_evidence") == CURRENT_EVIDENCE_STATE
        )
        current["status"] = "PASS" if current_ok else "FAIL"
        if current_ok:
            current["release_id"] = release["ddfi_release_id"]

        # The worker rejects cutoffs after the current UTC date; a local-time
        # "tomorrow" can still equal the server's today around midnight.
        future = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        ahead, body = execute(
            "future_cutoff_rejected",
            {
                "operation": "benchmark_context",
                "dataset_id": "cost_of_capital",
                "subject": subjects["cost_of_capital"],
                "measures": [EXPECTED["cost_of_capital"][2]],
                "as_of": future,
            },
        )
        ahead["status"] = (
            "PASS" if ahead["http_status"] == 400 and ahead.get("error_code") == "INVALID_REQUEST" else "FAIL"
        )

    for dataset_id in EXPECTED:
        subject = subjects.get(dataset_id)
        if not subject:
            continue
        check, body = execute(
            f"historical:{dataset_id}:{ELIGIBLE_CUTOFF}",
            {
                "operation": "benchmark_context",
                "dataset_id": dataset_id,
                "subject": subject,
                "measures": [EXPECTED[dataset_id][2]],
                "as_of": ELIGIBLE_CUTOFF,
            },
        )
        provenance, reason = evidence_provenance(body, dataset_id=dataset_id) if body else (None, "no response")
        if check["http_status"] == 200 and provenance is not None:
            check["status"] = "PASS"
            check["provenance"] = provenance
        elif check["status"] != "PASS":
            check["reason"] = reason or "evidence-bound historical selection failed"

    passing = [c for c in checks if c["status"] == "PASS"]
    status = "PASS" if len(passing) == len(checks) else "FAIL"
    return {
        "schema_version": "zion-benchmark-evidence-acceptance-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "origin": base,
        "status": status,
        "authenticated_api_verified": any(
            c["status"] == "PASS" for c in checks if c["name"].startswith(("historical:", "current:"))
        ),
        "evidence_catalog_sha256": CATALOG_SHA256,
        "evidence_bound_datasets": sorted(historical),
        "checks": checks,
        "limitations": [
            "transport and provenance acceptance, not independent source reconciliation",
            "day-precision retrieval evidence cannot establish intraday ordering",
            "rejection controls are exercised through request inputs, not corrupted production objects",
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
        print("Acceptance configuration invalid; supply a credential-free origin and runtime token.")
        return 2
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"])}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
