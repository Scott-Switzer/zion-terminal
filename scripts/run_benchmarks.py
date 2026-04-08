#!/usr/bin/env python3
"""Benchmark runner for Zion Terminal.

Usage:
    python scripts/run_benchmarks.py
    python scripts/run_benchmarks.py --output docs/benchmarks/latest.json
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


def get_git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT
        ).decode().strip()
    except Exception:
        return "unknown"


def count_tests() -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    for line in result.stdout.split("\n"):
        if "passed" in line:
            import re
            m = re.search(r"(\d+) passed", line)
            if m:
                return {"total_passed": int(m.group(1))}
    return {"total_passed": 0}


def count_parser_corpus() -> dict:
    corpus_path = FIXTURES_DIR / "sample_queries.json"
    if corpus_path.exists():
        data = json.loads(corpus_path.read_text())
        return {"corpus_size": len(data)}
    return {"corpus_size": 0}


def count_fixtures() -> dict:
    html_files = list(FIXTURES_DIR.glob("*.html"))
    return {
        "html_fixtures": len(html_files),
        "fixture_names": [f.name for f in html_files],
    }


def run_benchmarks() -> dict:
    """Run all offline benchmarks and collect metrics."""
    report = {
        "run_date": datetime.utcnow().isoformat() + "Z",
        "commit": get_git_hash(),
        "test_suite": count_tests(),
        "parser_corpus": count_parser_corpus(),
        "fixtures": count_fixtures(),
        "notes": [
            "All benchmarks run offline against local fixtures",
            "No live API calls — all data is mocked or from fixtures",
            "Arelle not installed in standard dev environment",
        ],
    }
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run Zion Terminal benchmarks")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    report = run_benchmarks()

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2))
        print(f"Report written to {args.output}")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
