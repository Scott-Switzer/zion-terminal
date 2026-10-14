"""Reproduce metadata coverage of evaluated SEC source members without provider requests."""

import json
from collections import Counter

from materialize_sec_engine import OUT


def main():
    manifest = json.loads((OUT / "engine-manifest.json").read_text())
    concepts = set()
    periods = set()
    ends = []
    history_ends = []
    families = Counter()
    rejections = Counter()
    metrics = Counter()
    for cik in manifest["companies"]:
        data = json.loads((OUT / (cik + ".history.json")).read_text())
        assert data["release_id"] == manifest["release_id"]
        families[data["policy"]["family"]] += 1
        rejections.update(data["rejections"])
        for row in data["observations"]:
            concepts.add(row["concept"])
            periods.add((row["period_start"], row["period_end"]))
            ends.append(row["period_end"])
        annual = json.loads((OUT / (cik + ".annual.expected.json")).read_text())
        assert annual["source_release_id"] == manifest["release_id"]
        metrics.update(row["metric"] for row in annual["observations"])
        history_ends.extend(row["period_end"] for row in annual["observations"])
    reasons = Counter(x["reason"] for x in manifest["quarantined"].values())
    report = {
        "release_id": manifest["release_id"],
        "archive_cik_names": manifest["archive_issuers"],
        "processed": len(manifest["companies"]) + len(manifest["quarantined"]),
        "usable": len(manifest["companies"]),
        "quarantined": manifest["quarantined"],
        "source_validation_failures": reasons["SOURCE_IDENTITY_MISMATCH"],
        "mapping_or_reporting_unsupported": reasons["NO_VALID_MAPPED_FINANCIAL_HISTORY"],
        "normalized_concepts": sorted(concepts),
        "unique_normalized_period_contexts": len(periods),
        "normalized_period_end_range": [min(ends), max(ends)],
        "usable_annual_period_end_range": [min(history_ends), max(history_ends)],
        "statement_families": dict(families),
        "source_rejections": dict(rejections),
        "annual_metric_observations": dict(metrics),
        "full_archive_financial_counts": ("STORED_CENSUS_DECLARED_NOT_RECOUNTED;"
                                          "EVALUATED_COHORT_INDEPENDENTLY_VERIFIED"),
    }
    (OUT / "coverage-summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ["quarantined", "normalized_concepts", "source_rejections"]
            }
        )
    )


if __name__ == "__main__":
    main()
