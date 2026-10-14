"""Bounded SEC-only terminal workflow; auth stays in the local credential file."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def response_release(value):
    page = value.get("pages", [value])[0]
    release = page.get("release_id") or page.get("provenance", {}).get("release_id")
    if not release:
        raise ValueError("WORKFLOW_RESPONSE_RELEASE_REQUIRED")
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciks", required=True, help="Two to twenty explicitly selected CIKs")
    parser.add_argument("--search", required=True, help="Company discovery search term")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--date-from", default="2020-01-01")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    commands = []
    release = None

    def run(name, action, *options):
        nonlocal release
        path = args.output_dir / (name + ".json")
        command = [
            sys.executable,
            "-m",
            "zion_terminal.private_research",
            action,
            "--base-url",
            args.base_url,
            "--output",
            str(path),
            *options,
        ]
        if release:
            command += ["--expected-release-id", release]
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(name + ": " + result.stderr.strip())
        value = json.loads(path.read_text())
        release = response_release(value)
        commands.append(command)
        return value

    run("discovery", "companies", "--search", args.search, "--limit", "20")
    metrics = "revenue,revenue_growth,operating_margin,operating_cash_flow,free_cash_flow"
    for frequency in ("annual", "quarterly"):
        run(
            frequency,
            "compare",
            "--ciks",
            args.ciks,
            "--frequency",
            frequency,
            "--metrics",
            metrics,
            "--date-from",
            args.date_from,
            "--as-of",
            args.as_of,
        )
    run(
        "screen",
        "screen",
        "--metrics",
        "revenue_growth",
        "--minimum",
        "0.1",
        "--as-of",
        args.as_of,
        "--limit",
        "100",
        "--all-pages",
    )
    run(
        "provenance",
        "provenance",
        "--cik",
        args.ciks.split(",")[0],
        "--metrics",
        "revenue,operating_income,operating_cash_flow,free_cash_flow",
        "--frequency",
        "annual",
        "--date-from",
        args.date_from,
        "--as-of",
        args.as_of,
    )
    manifest = {
        "release_id": release,
        "commands": commands,
        "qualification": "SEC_RESEARCH_ONLY;NO_PRICE_OR_PEER_ASSUMPTIONS",
        "as_of": args.as_of,
        "exports": sorted(p.name for p in args.output_dir.glob("*.json")),
    }
    with (args.output_dir / "workflow.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
    print(
        json.dumps(
            {"status": "COMPLETE", "release_id": release, "output_dir": str(args.output_dir)}
        )
    )


if __name__ == "__main__":
    main()
