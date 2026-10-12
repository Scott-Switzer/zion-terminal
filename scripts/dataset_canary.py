"""Operator-run live regression canary for the private dataset registry.

This is the live half of the canary split. The offline half runs on a schedule in
GitHub Actions against the synthetic fixture tree (see
``.github/workflows/datasets-regression-canary.yml``) and needs no credentials.
The live half needs the authenticated loopback operator bridge to the private
``zion-datasets`` service, so it runs where that bridge exists: on the operator's
machine, under the operator's identity. It is deliberately not wired to a hosted
runner, because the private worker must not be published to make scheduling
easier.

Usage::

    # 1. first run establishes the approved baseline (explicit, audited)
    PYTHONPATH=src python scripts/dataset_canary.py --as-of 2026-10-11 \\
        --accept-baseline --reason "M1 approved baseline" --actor "$USER"

    # 2. later runs compare a fresh live report against it
    PYTHONPATH=src python scripts/dataset_canary.py --as-of 2026-10-12

Exit status follows the comparison severity scale (0 PASS/INFO, 1 WARNING,
2 FAIL) so a scheduled or manual invocation can gate on it. A run that ends in
FAIL never advances the baseline; advancement is explicit, reasoned and appended
to an auditable ``baseline_history.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from zion_terminal.datasets.compare import (
    EXIT_STATUS,
    compare_reports,
    load_report,
    write_comparison,
)
from zion_terminal.datasets.registry import FinancialDatasetRegistry
from zion_terminal.datasets.report import RegistryReport, write_report
from zion_terminal.datasets.transport import (
    DEFAULT_BRIDGE_URL,
    HttpDatasetBridge,
    validate_bridge_url,
)

DEFAULT_OUT = Path("artifacts/registry-m2")
DEFAULT_BASELINE = DEFAULT_OUT / "approved-baseline.json"
#: The sanitized baseline is committed, so a fresh checkout can still compare the
#: public fields before the operator has created a private baseline.
PUBLIC_BASELINE = Path("docs/status/datasets-m2-baseline.summary.json")


def build_registry(bridge: str, fixture: str | None) -> FinancialDatasetRegistry:
    if fixture:
        from zion_terminal.datasets.cli import load_fixture_bridge

        return FinancialDatasetRegistry(load_fixture_bridge(Path(fixture)))
    validate_bridge_url(bridge)
    return FinancialDatasetRegistry(HttpDatasetBridge(bridge))


def resolve_baseline(path: Path) -> tuple[Path | None, list[str]]:
    """Find the approved baseline, or report that the first run must record one."""
    notes: list[str] = []
    if path.is_file():
        return path, notes
    if PUBLIC_BASELINE.is_file():
        notes.append(
            f"{path} not found; falling back to the committed sanitized baseline "
            f"{PUBLIC_BASELINE}, so provenance checks are not evaluated"
        )
        return PUBLIC_BASELINE, notes
    notes.append(
        f"no private baseline at {path} and no committed baseline at {PUBLIC_BASELINE}; "
        "this run can only bootstrap one with --accept-baseline --reason"
    )
    return None, notes


def append_history(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--tickers", default="AAPL,MSFT,NVDA")
    parser.add_argument("--periods", type=int, default=3)
    parser.add_argument("--sample", type=int, default=3)
    parser.add_argument("--bridge", default=DEFAULT_BRIDGE_URL)
    parser.add_argument("--fixture", default=None, help="replay a synthetic tree instead of live")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--accept-baseline",
        action="store_true",
        help="record the candidate as the new approved baseline (never after a FAIL)",
    )
    parser.add_argument(
        "--allow-warning-baseline",
        action="store_true",
        help="also accept a baseline when the comparison is only WARNING",
    )
    parser.add_argument("--reason", default=None, help="required with --accept-baseline")
    parser.add_argument("--actor", default=None)
    parser.add_argument("--clock", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    tickers = [ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()]
    baseline_path, notes = resolve_baseline(args.baseline)
    if baseline_path is None and not (args.accept_baseline and args.reason):
        print(notes[-1], file=sys.stderr)
        return 2

    registry = build_registry(args.bridge, args.fixture)
    mode = "fixture" if args.fixture else "live"
    candidate_dir = args.out / "candidate"
    report = RegistryReport(registry, mode=mode).build(
        tickers, as_of=args.as_of, periods=args.periods, artifact_sample=args.sample
    )
    written = write_report(report, candidate_dir)

    comparison = None
    comparison_written: dict[str, str] = {}
    if baseline_path is not None:
        comparison = compare_reports(
            load_report(baseline_path), load_report(candidate_dir / "report.json")
        )
        comparison_written = write_comparison(comparison, args.out / "comparison")
    severity = None if comparison is None else comparison["overall_severity"]

    accepted = False
    if args.accept_baseline:
        if severity is None and not args.reason:
            print("--accept-baseline requires --reason so the change is auditable", file=sys.stderr)
            return 2
        if severity == "FAIL":
            print(
                "refusing to advance the baseline: the comparison is FAIL "
                "(a failed run never overwrites the known-good baseline)",
                file=sys.stderr,
            )
            return 2
        if severity == "WARNING" and not args.allow_warning_baseline:
            print(
                "refusing to advance the baseline on WARNING without --allow-warning-baseline",
                file=sys.stderr,
            )
            return 1
        if not args.reason:
            print("--accept-baseline requires --reason so the change is auditable", file=sys.stderr)
            return 2
        previous = None
        if baseline_path is not None and baseline_path.is_file():
            previous = load_report(baseline_path).get("report_sha256")
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text((candidate_dir / "report.json").read_text())
        append_history(
            args.out / "baseline_history.jsonl",
            {
                "recorded_at": recorded_at,
                "actor": args.actor,
                "reason": args.reason,
                "as_of": args.as_of,
                "tickers": tickers,
                "mode": mode,
                "severity": severity,
                "bootstrap": comparison is None,
                "baseline_sha256": report["report_sha256"],
                "previous_baseline_sha256": previous,
                "comparison_sha256": None
                if comparison is None
                else comparison["comparison_sha256"],
                "comparison_summary": comparison_written.get("summary"),
            },
        )
        accepted = True

    payload = {
        "as_of": args.as_of,
        "mode": mode,
        "tickers": tickers,
        "baseline": {
            "path": None if baseline_path is None else str(baseline_path),
            "notes": notes,
        },
        "overall_severity": severity,
        "exit_status": 0 if severity is None else EXIT_STATUS[severity],
        "finding_counts": {} if comparison is None else comparison["finding_counts"],
        "companies": {}
        if comparison is None
        else {
            company["ticker"]: {
                "severity": company["severity"],
                "findings": [item["code"] for item in company["findings"]],
            }
            for company in comparison["companies"]
        },
        "baseline_advanced": accepted,
        "report_sha256": report["report_sha256"],
        "comparison_sha256": None if comparison is None else comparison["comparison_sha256"],
        "written": {**written, **comparison_written},
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if severity is None else EXIT_STATUS[severity]


if __name__ == "__main__":
    raise SystemExit(main())
