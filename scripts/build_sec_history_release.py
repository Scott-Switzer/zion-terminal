"""Build public-only immutable history payloads; does not update existing pointers."""
# ruff: noqa: E501

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "deploy/cloudflare-query/src"))
from extract_sec_history_sources import OUT
from sec_history import (
    DEBT,
    DEBT_QUARANTINED,
    REGISTRY,
    REVENUE_CONCEPTS,
    VERSION,
    encoded,
    history,
    normalize_sources,
)


def main():
    lineage = json.loads((OUT / "source-lineage.json").read_text())
    maximum_as_of = min(x["archive"]["snapshot_date"] for x in lineage) + "T00:00:00Z"
    registry = {
        "version": VERSION,
        "metrics": REGISTRY,
        "company_debt_components": DEBT,
        "debt_quarantined_companies": sorted(DEBT_QUARANTINED),
        "debt_scope_note": "DebtCurrent includes current capital leases; LongTermDebtNoncurrent excludes capital leases, so noncurrent capital leases are also mandatory. Apple/Costco overlap requires filing-level confirmation; no operating-lease substitution.",
        "company_revenue_concepts": REVENUE_CONCEPTS,
        "fiscal_quarter_days": {"default": [75, 105], "0000909832": [75, 120]},
        "revenue_policy": "Native concept retained; overlapping unequal concepts rejected; TTM cannot span concept changes.",
    }
    implementation_sha = hashlib.sha256(
        (Path(__file__).parents[1] / "deploy/cloudflare-query/src/sec_history.py").read_bytes()
    ).hexdigest()
    registry["implementation_sha256"] = implementation_sha
    registry["dependency_sha256"] = {
        name: hashlib.sha256(
            (Path(__file__).parents[1] / "deploy/cloudflare-query/src" / name).read_bytes()
        ).hexdigest()
        for name in ("temporal_core.py", "new_york_tz.py")
    }
    rbytes = encoded(registry)
    rsha = hashlib.sha256(rbytes).hexdigest()
    release = hashlib.sha256(
        encoded(
            {
                "sources": [(x["cik"], x["member_sha256"]) for x in lineage],
                "registry_sha256": rsha,
                "version": VERSION,
                "maximum_as_of": maximum_as_of,
            }
        )
    ).hexdigest()
    prefix = "gold/sec-financial-history/releases/" + release + "/"
    manifest = {
        "schema_version": VERSION,
        "release_id": release,
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "registry_sha256": rsha,
        "maximum_as_of": maximum_as_of,
        "date_from": "2015-01-01",
        "date_to": maximum_as_of[:10],
        "companies": {},
    }
    coverage = []
    for source in lineage:
        cik = source["cik"]
        raw = (OUT / (cik + ".companyfacts.json")).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["member_sha256"]
        data = normalize_sources(json.loads(raw, parse_float=Decimal), cik, source["member_sha256"])
        # Include earlier observations needed for TTM/quarter differences, retaining every revision.
        data["observations"] = [r for r in data["observations"] if r["period_end"] >= "2013-01-01"]
        data["source_lineage"] = source
        data["release_id"] = release
        data["registry_sha256"] = rsha
        body = encoded(data)
        digest = hashlib.sha256(body).hexdigest()
        assert len(body) <= 16 * 1048576
        (OUT / (cik + ".history.json")).write_bytes(body)
        manifest["companies"][cik] = {
            "key": prefix + cik + ".json",
            "sha256": digest,
            "bytes": len(body),
            "label": source["label"],
            "member_sha256": source["member_sha256"],
        }
        periods = {}
        for frequency in ["annual", "quarterly", "ttm"]:
            result = history(
                data,
                metrics=list(REGISTRY),
                frequency=frequency,
                date_from=manifest["date_from"],
                date_to=manifest["date_to"],
                as_of=manifest["maximum_as_of"],
            )
            (OUT / (cik + "." + frequency + ".expected.json")).write_bytes(encoded(result))
            periods[frequency] = {
                m: {
                    "observations": sum(r["metric"] == m for r in result["observations"]),
                    "gaps": sum(g["metric"] == m for g in result["coverage_gaps"]),
                }
                for m in REGISTRY
            }
        coverage.append(
            {
                "cik": cik,
                "label": source["label"],
                "normalized_revisions": len(data["observations"]),
                "bytes": len(body),
                "coverage": periods,
                "rejections": data["rejections"],
                "debt_required_components": DEBT[cik],
            }
        )
        print(
            json.dumps(
                {
                    "cik": cik,
                    "revisions": len(data["observations"]),
                    "bytes": len(body),
                    "annual_counts": {m: v["observations"] for m, v in periods["annual"].items()},
                }
            ),
            flush=True,
        )
    (OUT / "history-manifest.json").write_bytes(encoded(manifest))
    (OUT / "metric-registry.json").write_bytes(rbytes)
    (OUT / "company-coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    Path("contracts/sec-financial-metric-registry-v1.json").write_text(
        json.dumps(registry, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "release_id": release,
                "manifest_sha256": hashlib.sha256(encoded(manifest)).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
