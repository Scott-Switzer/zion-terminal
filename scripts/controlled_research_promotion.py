"""Staging-only activation with automatic verification and verified rollback.

An already uploaded immutable Worker version is activated; no provider request,
archive mutation, public routing change or production CURRENT write occurs.
"""

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

WORKER = "zion-financial-serving-v2-thin-staging"
VERSION_PATTERN = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"


def single_active_version(deployments):
    """Reject split traffic and stale array ordering when checking deployment state."""
    latest = max(deployments, key=lambda row: row["created_on"])
    versions = latest.get("versions", [])
    if len(versions) != 1 or versions[0].get("percentage") != 100:
        raise RuntimeError("SPLIT_OR_UNKNOWN_ACTIVE_DEPLOYMENT")
    version = versions[0].get("version_id", "")
    if not re.fullmatch(VERSION_PATTERN, version):
        raise RuntimeError("INVALID_ACTIVE_VERSION")
    return version


def wait_for_version(read_version, expected, *, attempts=8, pause=time.sleep):
    for attempt in range(attempts):
        if read_version() == expected:
            return
        if attempt + 1 < attempts:
            pause(2)
    raise RuntimeError("EDGE_VERSION_NOT_PROPAGATED")


def run_controlled(*, preflight, activate, verify, rollback, verify_previous, record):
    report = {"status": "PREFLIGHT", "events": [], "rollback_verified": False}

    def stage(name):
        report["events"].append(name)
        report["status"] = name
        record(dict(report))

    try:
        preflight()
    except Exception as exc:
        report.update(
            status="PREFLIGHT_FAILED", error_class=type(exc).__name__, error_code=str(exc)
        )
        record(report)
        return report
    stage("ACTIVATING")
    try:
        report["candidate_version_id"] = activate()
        stage("VERIFYING")
        report["verification"] = verify()
        stage("PASS")
    except Exception as exc:
        report["error_class"] = type(exc).__name__
        report["error_code"] = str(exc)
        stage("ROLLING_BACK")
        try:
            rollback()
            report["previous_verification"] = verify_previous()
            report["rollback_verified"] = True
            stage("ROLLED_BACK")
        except Exception as restore_error:
            report["rollback_error_class"] = type(restore_error).__name__
            report["rollback_error_code"] = str(restore_error)
            stage("ROLLBACK_UNVERIFIED")
    record(report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--wrangler", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--previous-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--rollback-drill", action="store_true")
    args = parser.parse_args()
    if not args.activate:
        parser.error("--activate explicitly selects the staging activation operation")
    if args.output.exists() or any(
        not re.fullmatch(VERSION_PATTERN, v)
        for v in (args.candidate_version, args.previous_version)
    ):
        parser.error("new evidence output and valid immutable version IDs required")
    repo = Path(__file__).resolve().parents[1]
    publication = json.loads(args.publication.read_text())
    root_path = args.publication.parent / "manifest.json"
    if not root_path.exists():
        root_path = args.publication.parent / "serving-manifest.json"
    root_raw = root_path.read_bytes()
    root = json.loads(root_raw)
    from sec_engine_acceptance import request

    baseline = {}

    def command(parts, log):
        with log.open("w") as stream:
            result = subprocess.run(
                [str(args.wrangler), *parts, "--config", str(args.config)],
                cwd=repo,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        if result.returncode:
            raise RuntimeError("WRANGLER_OPERATION_FAILED")

    def active_version():
        for attempt in range(3):
            result = subprocess.run(
                [str(args.wrangler), "deployments", "list", "--json", "--config", str(args.config)],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=45,
            )
            try:
                if result.returncode:
                    raise RuntimeError("ACTIVE_DEPLOYMENT_UNAVAILABLE")
                return single_active_version(json.loads(result.stdout))
            except (json.JSONDecodeError, RuntimeError):
                diagnostic = args.output.with_suffix(f".state-{attempt}.log")
                diagnostic.write_text(result.stdout + "\nSTDERR\n" + result.stderr)
                diagnostic.chmod(0o600)
                if attempt == 2:
                    raise RuntimeError("ACTIVE_DEPLOYMENT_UNAVAILABLE") from None
                time.sleep(1)

    def record(report):
        report = dict(
            report,
            previous_version_id=args.previous_version,
            candidate_version_id=args.candidate_version,
            rollback_drill=args.rollback_drill,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_suffix(".tmp")
        temp.write_text(json.dumps(report, indent=2) + "\n")
        temp.chmod(0o600)
        temp.replace(args.output)

    def current_history(release=None):
        params = dict(
            cik="0000789019",
            metrics="revenue",
            frequency="annual",
            date_from="2020-01-01",
            limit=1000,
        )
        if release:
            params["release_id"] = release
        body, measure = request("/private/v1/financial-history", params)
        if measure["http_status"] != 200 or body["pagination"]["next_cursor"] is not None:
            raise RuntimeError("HISTORICAL_BASELINE_UNAVAILABLE")
        digest = hashlib.sha256(
            json.dumps(body["observations"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return dict(
            release_id=body["provenance"]["release_id"],
            observations_sha256=digest,
            observations=len(body["observations"]),
        )

    def preflight():
        config = json.loads(args.config.read_text())
        if config.get("name") != WORKER or publication.get("status") != "PASS":
            raise RuntimeError("STAGING_PUBLICATION_GATE_FAILED")
        expected = publication["root"]
        if hashlib.sha256(root_raw).hexdigest() != expected["sha256"]:
            raise RuntimeError("PUBLICATION_HASH_MISMATCH")
        variables = config["vars"]
        if (
            variables.get("UNIFIED_RESEARCH_MANIFEST_KEY") != expected["key"]
            or variables.get("UNIFIED_RESEARCH_MANIFEST_SHA256") != expected["sha256"]
        ):
            raise RuntimeError("CONFIGURATION_NOT_PINNED")
        if (
            variables.get("SEC_ENGINE_MANIFEST_KEY") != root["financial_manifest_ref"]["key"]
            or variables.get("SEC_ENGINE_MANIFEST_SHA256")
            != root["financial_manifest_ref"]["sha256"]
        ):
            raise RuntimeError("FINANCIAL_CONFIGURATION_MISMATCH")
        for name, reference in root["implementation_refs"].items():
            source = repo / "deploy/cloudflare-query/src" / name
            if (
                source.parent != (repo / "deploy/cloudflare-query/src")
                or not source.is_file()
                or hashlib.sha256(source.read_bytes()).hexdigest() != reference["sha256"]
            ):
                raise RuntimeError("RUNTIME_IMPLEMENTATION_DRIFT")
        if subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", "deploy/cloudflare-query"], cwd=repo
        ).returncode:
            raise RuntimeError("WORKER_SOURCES_UNCOMMITTED")
        if active_version() != args.previous_version:
            raise RuntimeError("PREVIOUS_VERSION_DRIFT")
        baseline.update(current_history())

    def activate():
        command(
            [
                "versions",
                "deploy",
                args.candidate_version + "@100",
                "--yes",
                "--message",
                "Controlled unified research staging promotion",
            ],
            args.output.with_suffix(".activate.log"),
        )
        return args.candidate_version

    def edge_version():
        body, measure = request("/v3/capabilities")
        if measure["http_status"] != 200:
            return None
        return body.get("worker_version_id")

    def verify():
        if active_version() != args.candidate_version:
            raise RuntimeError("CANDIDATE_VERSION_NOT_ACTIVE")
        wait_for_version(edge_version, args.candidate_version)
        report = []
        for name, route, params in (
            ("overview", "research-overview", {"cik": "0000789019"}),
            (
                "flow_valuation",
                "research-valuation",
                {
                    "cik": "0000001800",
                    "metrics": "operating_margin,net_margin,revenue_growth",
                    "frequency": "annual",
                    "as_of": "2019-10-31T00:00:00Z",
                    "date_to": "2019-10-31",
                    "date_from": "2014-01-01",
                },
            ),
            ("certification", "research-certification", {"cik": "0000320193", "limit": 2}),
            ("private_market_history", "research-prices", {"symbol": "WMT", "limit": 2}),
        ):
            for attempt in range(5):
                body, measure = request(
                    "/private/v1/" + route,
                    dict(params, expected_release_id=root["financial_release_id"]),
                )
                if measure["http_status"] != 404:
                    break
                diagnostic = args.output.with_suffix(f".{name}-404-{attempt}.json")
                diagnostic.write_text(json.dumps(body))
                diagnostic.chmod(0o600)
                time.sleep(2)
            if (
                measure["http_status"] != 200
                or body.get("provenance", {}).get(
                    "research_release_id", body.get("research_release_id")
                )
                != root["release_id"]
            ):
                raise RuntimeError(
                    "LIVE_CANDIDATE_VERIFICATION_FAILED:"
                    + name
                    + ":"
                    + str(measure["http_status"])
                    + ":"
                    + str(body.get("error", {}).get("code", "RELEASE_MISMATCH"))
                )
            report.append(dict(name=name, status="PASS", **measure))
        old = current_history(baseline["release_id"])
        if old != baseline:
            raise RuntimeError("HISTORICAL_REPRODUCTION_FAILED")
        if args.rollback_drill:
            raise RuntimeError("INTENTIONAL_VERIFIED_ROLLBACK_DRILL")
        return dict(status="PASS", checks=report, historical_reproduction=old)

    def rollback():
        command(
            [
                "versions",
                "deploy",
                args.previous_version + "@100",
                "--yes",
                "--message",
                "Automatic rollback after unified research verification",
            ],
            args.output.with_suffix(".rollback.log"),
        )

    def verify_previous():
        if active_version() != args.previous_version:
            raise RuntimeError("ROLLBACK_VERSION_NOT_ACTIVE")
        wait_for_version(edge_version, args.previous_version)
        actual = current_history(baseline["release_id"])
        if actual != baseline:
            raise RuntimeError("ROLLBACK_HISTORY_UNVERIFIED")
        return dict(status="PASS", historical_reproduction=actual)

    result = run_controlled(
        preflight=preflight,
        activate=activate,
        verify=verify,
        rollback=rollback,
        verify_previous=verify_previous,
        record=record,
    )
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in ("status", "candidate_version_id", "rollback_verified", "error_class")
            }
        )
    )
    return (
        0
        if result["status"] == "PASS" or (args.rollback_drill and result["status"] == "ROLLED_BACK")
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
