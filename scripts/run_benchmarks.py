#!/usr/bin/env python3
"""Benchmark runner for Zion Terminal.

Produces real benchmark data from the actual codebase. No fabricated results.

Usage:
    python scripts/run_benchmarks.py
    python scripts/run_benchmarks.py --output docs/benchmarks/latest.json
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"

# Ensure src is importable
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def get_git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT
        ).decode().strip()
    except Exception:
        return "unknown"


def get_version() -> str:
    try:
        from zion_terminal import __version__
        return __version__
    except Exception:
        return "unknown"


def run_tests() -> dict:
    """Run pytest and capture real pass/fail counts."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no", "--no-header", "-p", "no:benchmark"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    import re
    total = 0
    passed = 0
    failed = 0
    for line in result.stdout.split("\n"):
        m = re.search(r"(\d+) passed", line)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", line)
        if m:
            failed = int(m.group(1))
    total = passed + failed
    return {"total": total, "passed": passed, "failed": failed}


def count_parser_corpus() -> dict:
    """Count the actual parser test corpus."""
    corpus_path = FIXTURES_DIR / "sample_queries.json"
    if corpus_path.exists():
        data = json.loads(corpus_path.read_text())
        return {"corpus_size": len(data), "corpus_file": "tests/fixtures/sample_queries.json"}
    return {"corpus_size": 0, "corpus_file": "not found"}


def benchmark_fixtures() -> list[dict]:
    """Run the converter+segmenter+verification on all HTML fixtures.

    For fixtures that have a matching company_facts JSON file, runs
    company-facts-based reconciliation to prove verification works.
    """
    from zion_terminal.pipeline.filing_pipeline import FilingPipeline

    pipeline = FilingPipeline()
    html_files = sorted(FIXTURES_DIR.glob("*.html"))
    company_facts_path = FIXTURES_DIR / "sample_company_facts.json"
    company_facts = None
    if company_facts_path.exists():
        company_facts = json.loads(company_facts_path.read_text())

    # Fixtures that should be benchmarked WITH company facts reconciliation
    _RECONCILIATION_FIXTURES = {
        "sample_filing_with_tables.html",
        "sample_filing_scale_mismatch.html",
    }

    results = []

    for f in html_files:
        html = f.read_text()
        metadata: dict = {"fixture": f.name}
        # Attach company facts for reconciliation-eligible fixtures
        if f.name in _RECONCILIATION_FIXTURES and company_facts:
            metadata["company_facts"] = company_facts

        start = time.perf_counter()
        result = pipeline.process(
            html=html,
            ticker="BENCH",
            form="10-K",
            filing_date="2023-11-03",
            metadata=metadata,
        )
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        verification = result.verification or {}
        results.append({
            "name": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "success": result.success,
            "sections_extracted": result.section_count,
            "converter": result.converter_engine,
            "duration_ms": duration_ms,
            "raw_char_count": len(html),
            "markdown_char_count": result.markdown_char_count,
            "compression_ratio": round(result.markdown_char_count / max(len(html), 1), 3),
            "verification_status": verification.get("status", "unknown"),
            "verification_depth": verification.get("verification_depth", "none"),
            "reconciliation_status": verification.get("reconciliation_status", "not_run"),
            "period_match_mode": verification.get("period_match_mode", ""),
            "facts_matched": verification.get("facts_matched", 0),
            "warnings": result.warnings,
        })

    return results


def benchmark_parser_accuracy() -> dict:
    """Run parser accuracy on the real corpus."""
    corpus_path = FIXTURES_DIR / "sample_queries.json"
    if not corpus_path.exists():
        return {"error": "corpus not found"}

    from zion_terminal.orchestrator.intent_parser import IntentParser
    parser = IntentParser()
    corpus = json.loads(corpus_path.read_text())

    total = 0
    correct = 0
    category_stats: dict[str, dict] = {}

    for entry in corpus:
        query = entry.get("query", "")
        expected_intent = entry.get("expected_intent", "")
        # Use the expected intent as category if no explicit category field
        category = entry.get("category", expected_intent or "unknown")

        if not query or not expected_intent:
            continue

        total += 1
        parsed = parser.parse(query)

        is_correct = parsed.intent == expected_intent
        if is_correct:
            correct += 1

        if category not in category_stats:
            category_stats[category] = {"count": 0, "correct": 0}
        category_stats[category]["count"] += 1
        if is_correct:
            category_stats[category]["correct"] += 1

    categories = []
    for cat, stats in sorted(category_stats.items()):
        acc = stats["correct"] / stats["count"] if stats["count"] > 0 else 0
        categories.append({
            "category": cat,
            "count": stats["count"],
            "correct": stats["correct"],
            "accuracy": round(acc, 3),
        })

    return {
        "total_queries": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total > 0 else 0,
        "categories": categories,
    }


def check_arelle() -> bool:
    """Check if Arelle is actually installed."""
    try:
        from zion_terminal.pipeline.xbrl import is_available
        return is_available()
    except Exception:
        return False


def run_benchmarks() -> dict:
    """Run all offline benchmarks and collect real metrics."""
    test_results = run_tests()
    fixtures = benchmark_fixtures()
    parser = benchmark_parser_accuracy()
    arelle = check_arelle()

    # Count reconciliation-aware fixtures
    recon_count = sum(1 for f in fixtures if f.get("reconciliation_status", "") not in ("", "not_run", "no_company_facts"))
    recon_pass = sum(1 for f in fixtures if f.get("reconciliation_status") == "reconciled_pass")

    report = {
        "run_date": datetime.now(timezone.utc).isoformat(),
        "commit": get_git_hash(),
        "version": get_version(),
        "runner": "scripts/run_benchmarks.py",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "arelle_installed": arelle,
        "test_suite": test_results,
        "fixtures": fixtures,
        "fixtures_summary": {
            "total": len(fixtures),
            "passing": sum(1 for f in fixtures if f["success"]),
            "with_warnings": sum(1 for f in fixtures if f["warnings"]),
            "reconciliation_tested": recon_count,
            "reconciliation_passed": recon_pass,
        },
        "parser_accuracy": parser,
        "notes": [
            "All benchmarks run against local fixtures — no live API calls",
            f"Company-facts reconciliation ran on {recon_count} fixtures ({recon_pass} passed) — does NOT require Arelle",
            f"Arelle {'installed — XBRL instance validation available' if arelle else 'not installed — XBRL instance validation unavailable (company-facts reconciliation still works)'}",
            "Results are real measurements from the current codebase",
        ],
    }
    return report


def generate_markdown(report: dict) -> str:
    """Generate markdown report from JSON results."""
    lines = [
        "# Benchmark Report: Latest",
        "",
        f"**Run date:** {report['run_date'][:10]}",
        f"**Commit:** {report['commit']}",
        f"**Version:** {report['version']}",
        f"**Python:** {report['python_version']}",
        f"**Arelle:** {'Installed' if report['arelle_installed'] else 'Not installed'}",
        "",
        "---",
        "",
        "## Test Suite",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Total tests | {report['test_suite']['total']} |",
        f"| Passed | {report['test_suite']['passed']} |",
        f"| Failed | {report['test_suite']['failed']} |",
        "",
        "---",
        "",
        "## Fixture Processing",
        "",
        "| Fixture | Size (KB) | Success | Sections | Converter | Duration (ms) | Compression |",
        "|---|---|---|---|---|---|---|",
    ]

    for f in report["fixtures"]:
        lines.append(
            f"| `{f['name']}` | {f['size_kb']} | {'Yes' if f['success'] else 'No'} "
            f"| {f['sections_extracted']} | {f['converter']} | {f['duration_ms']} "
            f"| {f['compression_ratio']} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## Parser Accuracy",
        "",
    ])

    pa = report.get("parser_accuracy", {})
    if pa.get("total_queries"):
        lines.extend([
            f"**Corpus:** {pa['total_queries']} queries, **Accuracy:** {pa['accuracy']:.1%}",
            "",
            "| Category | Count | Correct | Accuracy |",
            "|---|---|---|---|",
        ])
        for cat in pa.get("categories", []):
            lines.append(f"| {cat['category']} | {cat['count']} | {cat['correct']} | {cat['accuracy']:.1%} |")
    else:
        lines.append("Parser corpus not found.")

    lines.extend([
        "",
        "---",
        "",
        "## Notes",
        "",
    ])
    for note in report.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run Zion Terminal benchmarks")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--markdown", default=None, help="Output markdown path")
    args = parser.parse_args()

    report = run_benchmarks()
    json_str = json.dumps(report, indent=2, default=str)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json_str)
        print(f"JSON report: {args.output}")
    else:
        print(json_str)

    md = generate_markdown(report)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(md)
        print(f"Markdown report: {args.markdown}")


if __name__ == "__main__":
    main()
