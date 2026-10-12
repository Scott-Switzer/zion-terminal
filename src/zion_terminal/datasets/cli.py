"""Command line access to the private financial dataset registry.

Examples::

    python -m zion_terminal.datasets catalog
    python -m zion_terminal.datasets discover --family serving_release_candidate
    python -m zion_terminal.datasets release --family serving_release_candidate --sample 3
    python -m zion_terminal.datasets report --tickers AAPL,MSFT,NVDA --as-of 2026-10-11

Live access needs the loopback operator bridge
(``npx wrangler dev --config deploy/datasets/client/wrangler.jsonc``). Passing
``--fixture DIR`` replays a synthetic fixture tree instead, which is how the
registry is exercised without any production access.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from zion_terminal.datasets.registry import FAMILY_INDEX, SOURCE_FAMILIES, FinancialDatasetRegistry
from zion_terminal.datasets.report import RegistryReport, write_report
from zion_terminal.datasets.transport import (
    DEFAULT_BRIDGE_URL,
    STORE_IDS,
    FixtureBridge,
    HttpDatasetBridge,
    TransportError,
    validate_bridge_url,
)


def load_fixture_bridge(root: Path) -> FixtureBridge:
    """Build an offline bridge from ``<root>/<store>/<path>`` files."""
    bridge = FixtureBridge()
    etags_path = root / "etags.json"
    etags = json.loads(etags_path.read_text()) if etags_path.exists() else {}
    for store in STORE_IDS:
        base = root / store
        if not base.is_dir():
            continue
        for item in sorted(base.rglob("*")):
            if not item.is_file():
                continue
            path = item.relative_to(base).as_posix()
            bridge.put(
                store, path, item.read_bytes(), etag=etags.get(f"{store}/{path}", "fixture-1")
            )
    return bridge


def build_registry(args: argparse.Namespace) -> FinancialDatasetRegistry:
    if args.fixture:
        bridge = load_fixture_bridge(Path(args.fixture))
        return FinancialDatasetRegistry(bridge)
    return FinancialDatasetRegistry(HttpDatasetBridge(args.bridge))


def _print(value: object, *, as_json: bool, stream: object = sys.stdout) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True, default=str), file=stream)
        return
    print(json.dumps(value, indent=2, sort_keys=True, default=str), file=stream)


def _cmd_catalog(args: argparse.Namespace) -> int:
    registry = build_registry(args)
    _print(registry.catalog(), as_json=True)
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    registry = build_registry(args)
    discovery = registry.discover(
        args.family,
        prefix=args.prefix,
        recursive=args.recursive,
        limit=args.limit,
        max_pages=args.max_pages,
    )
    _print(discovery.as_dict(), as_json=True)
    return 0 if discovery.quality != "BLOCKED" else 1


def _cmd_release(args: argparse.Namespace) -> int:
    registry = build_registry(args)
    release = registry.resolve_release(args.family)
    payload = release.as_dict(include_artifacts=args.artifacts)
    if args.sample:
        payload["artifact_sample"] = registry.sample_artifacts(release, limit=args.sample)
    _print(payload, as_json=True)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    registry = build_registry(args)
    tickers = [ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()]
    report = RegistryReport(registry, mode="fixture" if args.fixture else "live").build(
        tickers, as_of=args.as_of, periods=args.periods, artifact_sample=args.sample
    )
    written = write_report(report, args.out)
    summary = {
        "report_sha256": report["report_sha256"],
        "overall": {item["ticker"]: item["overall"] for item in report["companies"]},
        "state_counts": {item["ticker"]: item["state_counts"] for item in report["companies"]},
        "written": written,
    }
    _print(summary, as_json=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m zion_terminal.datasets", description=__doc__)
    parser.add_argument(
        "--bridge", default=DEFAULT_BRIDGE_URL, help="loopback operator bridge origin"
    )
    parser.add_argument("--fixture", help="replay a synthetic fixture tree instead of live storage")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "catalog", help="declared source families, not evidence of contents"
    ).set_defaults(func=_cmd_catalog)

    families = sorted(FAMILY_INDEX)
    discover = sub.add_parser("discover", help="bounded listing of one family prefix")
    discover.add_argument("--family", required=True, choices=families)
    discover.add_argument("--prefix", default=None)
    discover.add_argument("--recursive", action="store_true")
    discover.add_argument("--limit", type=int, default=25)
    discover.add_argument("--max-pages", type=int, default=2)
    discover.set_defaults(func=_cmd_discover)

    release = sub.add_parser("release", help="resolve a release pointer and validate its manifest")
    release.add_argument(
        "--family",
        required=True,
        choices=[
            family.family_id
            for family in SOURCE_FAMILIES
            if family.pointer_key or family.manifest_prefix
        ],
    )
    release.add_argument("--sample", type=int, default=0, help="HEAD this many manifest artifacts")
    release.add_argument("--artifacts", action="store_true", help="include the full artifact list")
    release.set_defaults(func=_cmd_release)

    report = sub.add_parser("report", help="build the per-company reconciliation report")
    report.add_argument("--tickers", default="AAPL,MSFT,NVDA")
    report.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat())
    report.add_argument("--periods", type=int, default=3)
    report.add_argument("--sample", type=int, default=3)
    report.add_argument("--out", default="artifacts/registry-m1")
    report.set_defaults(func=_cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.fixture is None:
            validate_bridge_url(args.bridge)
        return args.func(args)
    except TransportError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
