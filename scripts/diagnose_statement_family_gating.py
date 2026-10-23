"""Diagnose statement-family gating by issuer and observation period.

Read-only diagnosis over the frozen 2026-09-24 SEC archive members of the
diagnosed issuers. Nothing is uploaded and no policy, quarantine or registry
value is changed: the banking/insurance statement-family quarantine and the
capex/FCF/debt restrictions stay exactly as deployed.

The report answers, per issuer and per observation period:

1. which taxonomy evidence (lending/insurance concepts) is present in the
   filings covering that period — including when evidence stops after GE's
   portfolio changes;
2. that gating is issuer-global and therefore fail-closed in every period:
   no uncertified financial-family value is served in any observation period,
   including periods with no evidence rows at all;
3. that the quarantine set is preserved (revenue, operating_income,
   capital_expenditures, free_cash_flow, total_debt), and that lifting any of
   it requires audited statements and reviewed accounting policy — adding a
   concept to the registry is not a repair.

Run:  python3.12 scripts/diagnose_statement_family_gating.py
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))
sys.path.insert(0, str(Path(__file__).parent))

from build_revenue_override_candidate import (  # noqa: E402
    BUCKET,
    OUT as CANDIDATE_OUT,
    Remote,
    digest,
    write_once,
)
from materialize_sec_engine import extract  # noqa: E402
from sec_engine import (  # noqa: E402
    DEBT,
    DEBT_QUARANTINED,
    REGISTRY,
    REVENUE_CONCEPTS,
    history,
    normalize_sources,
    statement_policy,
)

OUT = Path(__file__).resolve().parents[3] / "artifacts/path1-statement-family-gating"

# The six diagnosed zero-revenue issuers (fixture scope in
# tests/unit/test_revenue_coverage_diagnosis.py). WMT is included as the
# unaffected-family control; GS/ALL/GE/C/JPM carry statement-family evidence.
ISSUERS = ["0000104169", "0000886982", "0000899051", "0000040545", "0000831001", "0000019617"]
GE = "0000040545"

EVIDENCE_TAGS = {
    "InterestIncomeExpenseAfterProvisionForLoanLosses",
    "InterestAndDividendIncomeOperating",
    "PremiumsEarnedNet",
    "InsurancePremiumsNet",
    "PolicyholderBenefitsAndClaimsIncurredNet",
    "LoansAndLeasesReceivableNetReportedAmount",
}
LENDING_TAGS = {
    "InterestIncomeExpenseAfterProvisionForLoanLosses",
    "InterestAndDividendIncomeOperating",
    "LoansAndLeasesReceivableNetReportedAmount",
}
INSURANCE_TAGS = {
    "PremiumsEarnedNet",
    "InsurancePremiumsNet",
    "PolicyholderBenefitsAndClaimsIncurredNet",
}

QUARANTINE_REASON = "FINANCIAL_STATEMENT_INDUSTRY_MAPPING_NOT_CERTIFIED"
STATEMENT_FAMILY_METRICS = (
    "revenue",
    "operating_income",
    "capital_expenditures",
    "free_cash_flow",
    "total_debt",
)


def period_evidence(company):
    """Evidence rows per observation period, by us-gaap concept.

    Each raw fact row is attributed to the fiscal period it ends in. Returns
    ``{period_end: {tag: {"rows": n, "filed": [min, max], "forms": [...]}}}``.
    """
    tags = company.get("facts", {}).get("us-gaap", {})
    periods = defaultdict(lambda: defaultdict(lambda: {"rows": 0, "filed": [], "forms": set()}))
    for tag in sorted(EVIDENCE_TAGS & set(tags)):
        for unit_rows in tags[tag].get("units", {}).values():
            for row in unit_rows:
                end = row.get("end")
                if not end:
                    continue
                slot = periods[end][tag]
                slot["rows"] += 1
                if row.get("filed"):
                    slot["filed"].append(row["filed"])
                if row.get("form"):
                    slot["forms"].add(row["form"])
    out = {}
    for end in sorted(periods):
        out[end] = {
            tag: {
                "rows": slot["rows"],
                "filed_min": min(slot["filed"]) if slot["filed"] else None,
                "filed_max": max(slot["filed"]) if slot["filed"] else None,
                "forms": sorted(slot["forms"]),
            }
            for tag, slot in sorted(periods[end].items())
        }
    return out


def candidate_period_ends(company):
    """Every period end any registry concept reports — including gated periods."""
    tags = company.get("facts", {}).get("us-gaap", {})
    concepts = {tag for rule in REGISTRY.values() for tag in rule["concepts"]}
    ends = set()
    for tag in concepts & set(tags):
        for unit_rows in tags[tag].get("units", {}).values():
            for row in unit_rows:
                if row.get("end"):
                    ends.add(row["end"])
    return ends


def served_by_period(result):
    """``{period_end: {metric: observations}}`` from a history() result."""
    out = defaultdict(lambda: defaultdict(int))
    for row in result["observations"]:
        out[row["period_end"]][row["metric"]] += 1
    return {end: dict(sorted(by_metric.items())) for end, by_metric in sorted(out.items())}


def frequency_gaps(result):
    out = defaultdict(set)
    for gap in result["coverage_gaps"]:
        out[gap["metric"]].add(gap["reason"])
    return {metric: sorted(reasons) for metric, reasons in sorted(out.items())}


def issuer_diagnosis(company, cik, member_sha256, *, date_to, as_of):
    policy = statement_policy(company)
    data = normalize_sources(company, cik, member_sha256)
    evidence = period_evidence(company)
    results = {}
    for frequency in ("annual", "quarterly", "ttm"):
        results[frequency] = history(
            data,
            metrics=list(REGISTRY),
            frequency=frequency,
            date_from="1930-01-01",
            date_to=date_to,
            as_of=as_of,
        )
    quarantined = policy["quarantined_metrics"]

    periods = sorted(
        set(evidence)
        | candidate_period_ends(company)
        | {row["period_end"] for row in results["annual"]["observations"]}
    )
    period_rows = []
    for end in periods:
        served = {f: served_by_period(results[f]).get(end, {}) for f in results}
        entry = {
            "period_end": end,
            "evidence_tags_ending_in_period": evidence.get(end, {}),
            "evidence_row_count": sum(slot["rows"] for slot in evidence.get(end, {}).values()),
            "served_metrics": served,
            "gating": {
                metric: quarantined[metric]
                for metric in STATEMENT_FAMILY_METRICS
                if metric in quarantined
            },
            "gated_out_in_period": sorted(
                metric
                for metric in quarantined
                if not any(metric in served[f] for f in served)
            ),
        }
        period_rows.append(entry)

    fail_closed = all(
        not any(metric in served[f] for f in entry["served_metrics"])
        for entry in period_rows
        for metric in quarantined
    )
    return {
        "cik": cik,
        "family": policy["family"],
        "classification_evidence": policy["classification_evidence"],
        "classification_qualification": policy["classification_qualification"],
        "revenue_concepts": policy["revenue_concepts"],
        "quarantined_metrics": quarantined,
        "debt_components": policy["debt_components"],
        "periods": period_rows,
        "gaps_by_metric": {f: frequency_gaps(results[f]) for f in results},
        "fail_closed_every_period": fail_closed,
    }


def ge_timeline(diagnosis):
    """GE evidence presence per fiscal year across its portfolio changes."""
    years = {}
    for entry in diagnosis["periods"]:
        year = entry["period_end"][:4]
        slot = years.setdefault(
            year, {"lending_rows": 0, "insurance_rows": 0, "tags": set(), "period_ends": []}
        )
        for tag, detail in entry["evidence_tags_ending_in_period"].items():
            slot["tags"].add(tag)
            if tag in LENDING_TAGS:
                slot["lending_rows"] += detail["rows"]
            if tag in INSURANCE_TAGS:
                slot["insurance_rows"] += detail["rows"]
        slot["period_ends"].append(entry["period_end"])
    out = {}
    for year in sorted(years):
        slot = years[year]
        out[year] = {
            "lending_rows": slot["lending_rows"],
            "insurance_rows": slot["insurance_rows"],
            "evidence_tags": sorted(slot["tags"]),
            "period_ends": slot["period_ends"],
            "gating_in_effect": bool(diagnosis["quarantined_metrics"]),
        }
    return out


def expected_quarantine(cik, family):
    """The deployed statement_policy quarantine map for an issuer."""
    if family == "financial_statement_evidence":
        return {metric: QUARANTINE_REASON for metric in STATEMENT_FAMILY_METRICS}
    if cik in DEBT and cik not in DEBT_QUARANTINED:
        return {}
    return {"total_debt": "COMPONENT_NONOVERLAP_NOT_CONFIRMED"}


def quarantine_preservation(diagnosies):
    """The deployed quarantine must survive this diagnosis unchanged."""
    preserved = {}
    for cik, diagnosis in sorted(diagnosies.items()):
        quarantine = diagnosis["quarantined_metrics"]
        expected = expected_quarantine(cik, diagnosis["family"])
        preserved[cik] = {
            "family": diagnosis["family"],
            "quarantined_metrics": quarantine,
            "matches_deployed_policy": quarantine == expected,
            "capex_fcf_debt_restrictions_released": sorted(
                m for m in ("capital_expenditures", "free_cash_flow", "total_debt") if m not in quarantine
            )
            if diagnosis["family"] == "financial_statement_evidence"
            else [],
        }
    return {
        "issuers": preserved,
        "statement_family_metrics": list(STATEMENT_FAMILY_METRICS),
        "quarantine_reason": QUARANTINE_REASON,
        "debt_quarantined_companies": sorted(DEBT_QUARANTINED),
        "debt_component_companies": sorted(DEBT),
        "issuer_revenue_pins": {cik: list(concepts) for cik, concepts in sorted(REVENUE_CONCEPTS.items())},
        "policy_unchanged_by_this_work": all(
            row["matches_deployed_policy"] for row in preserved.values()
        )
        and all(not row["capex_fcf_debt_restrictions_released"] for row in preserved.values()),
        "release_requires": "audited statements and reviewed accounting policy; a registry concept addition alone is not a repair",
    }


def universe_scan(catalog):
    """Statement-family gating for every catalog entry, read-only.

    The catalog shards carry each issuer's deployed policy and coverage counts,
    so the whole universe can be classified and checked for fail-closed serving
    without downloading a single raw member.
    """
    rows = []
    violations = []
    families = Counter()
    evidence_counts = Counter()
    quarantine_reasons = Counter()
    for cik in sorted(catalog):
        item = catalog[cik]
        policy = item.get("policy") or {}
        coverage = item.get("coverage") or {}
        if not policy:
            quarantine = item.get("quarantine") or {}
            reason = str(quarantine.get("reason"))
            families["ENTRY_QUARANTINED"] += 1
            quarantine_reasons[reason] += 1
            rows.append(
                {
                    "cik": cik,
                    "label": item.get("label"),
                    "family": "ENTRY_QUARANTINED",
                    "entry_quarantined": True,
                    "quarantine_reason": reason,
                    "source_eligibility": item.get("source_eligibility")
                    or quarantine.get("source_eligibility"),
                    "member_sha256": quarantine.get("member_sha256"),
                    "archive_compressed_bytes": (item.get("archive_entry") or {}).get(
                        "compressed_bytes"
                    ),
                    "fail_closed": True,
                }
            )
            continue
        family = policy.get("family")
        evidence = sorted(policy.get("classification_evidence") or [])
        quarantined = dict(policy.get("quarantined_metrics") or {})
        served = {
            frequency: {
                metric: coverage.get(frequency, {}).get(metric, {}).get("observations", 0)
                for metric in quarantined
            }
            for frequency in ("annual", "quarterly", "ttm")
        }
        leaked = sorted(
            frequency + ":" + metric
            for frequency, by_metric in served.items()
            for metric, observations in by_metric.items()
            if observations
        )
        if leaked:
            violations.append({"cik": cik, "leaked": leaked})
        released = sorted(
            metric
            for metric in ("capital_expenditures", "free_cash_flow", "total_debt")
            if family == "financial_statement_evidence" and metric not in quarantined
        )
        families[family] += 1
        evidence_counts.update(evidence)
        rows.append(
            {
                "cik": cik,
                "label": item.get("label"),
                "family": family,
                "entry_quarantined": False,
                "classification_evidence": evidence,
                "quarantined_metrics": quarantined,
                "quarantine_matches_policy_rule": quarantined
                == expected_quarantine(cik, family),
                "capex_fcf_debt_restrictions_released": released,
                "revenue_concepts": policy.get("revenue_concepts") or [],
                "member_sha256": (item.get("materialized") or {}).get("member_sha256"),
                "archive_compressed_bytes": (item.get("archive_entry") or {}).get(
                    "compressed_bytes"
                ),
                "served_observations_for_quarantined_metrics": served,
                "fail_closed": not leaked,
            }
        )
    financial = [
        row["cik"] for row in rows if row["family"] == "financial_statement_evidence"
    ]
    return {
        "issuers": rows,
        "totals": {
            "issuers": len(rows),
            "families": dict(sorted(families.items(), key=lambda kv: str(kv[0]))),
            "classification_evidence_counts": dict(sorted(evidence_counts.items())),
            "entry_quarantine_reasons": dict(sorted(quarantine_reasons.items())),
            "statement_family_issuers": len(financial),
            "statement_family_ciks": financial,
            "fail_closed_violations": violations,
            "fail_closed_every_issuer": not violations,
        },
    }


def universe_preservation(scan):
    """Aggregate quarantine-preservation evidence across the universe scan."""
    rows = [row for row in scan["issuers"] if not row.get("entry_quarantined")]
    return {
        "issuers_checked": len(rows),
        "entry_quarantined_issuers": sum(
            1 for row in scan["issuers"] if row.get("entry_quarantined")
        ),
        "all_quarantines_match_policy_rule": all(
            row["quarantine_matches_policy_rule"] for row in rows
        ),
        "capex_fcf_debt_restrictions_released": sorted(
            {
                metric
                for row in rows
                for metric in row.get("capex_fcf_debt_restrictions_released") or []
            }
        ),
        "fail_closed_every_issuer": scan["totals"]["fail_closed_every_issuer"],
        "fail_closed_violations": scan["totals"]["fail_closed_violations"],
        "statement_family_metrics": list(STATEMENT_FAMILY_METRICS),
        "quarantine_reason": QUARANTINE_REASON,
        "debt_quarantined_companies": sorted(DEBT_QUARANTINED),
        "debt_component_companies": sorted(DEBT),
        "issuer_revenue_pins": {
            cik: list(concepts) for cik, concepts in sorted(REVENUE_CONCEPTS.items())
        },
        "release_requires": (
            "audited statements and reviewed accounting policy; a registry concept "
            "addition alone is not a repair"
        ),
    }


def run_universe(remote, base, args):
    """Full-universe diagnosis: every issuer, then per-period depth for all
    statement-family issuers (streamed, resumable via universe-period-gating.jsonl).
    """
    catalog = {}
    for suffix in sorted(base["shards"]):
        catalog.update(json.loads(remote.get(base["shards"][suffix])))
    scan = universe_scan(catalog)
    preservation = universe_preservation(scan)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "universe-gating.json").write_text(
        json.dumps(
            {
                "base_release_id": base["release_id"],
                "date_to": base["date_to"],
                "maximum_as_of": base["maximum_as_of"],
                "read_only": True,
                "policy_changes_written": 0,
                **scan,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (OUT / "universe-quarantine-preservation.json").write_text(
        json.dumps(preservation, indent=2, sort_keys=True) + "\n"
    )

    path = OUT / "universe-period-gating.jsonl"
    period_rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                period_rows[row["cik"]] = row
    targets = [
        row
        for row in scan["issuers"]
        if row["family"] == "financial_statement_evidence" and row["cik"] not in period_rows
    ]
    planned_bytes = sum(row["archive_compressed_bytes"] or 0 for row in targets)
    if targets and not args.skip_periods:

        def diagnose(row):
            item = catalog[row["cik"]]
            raw = extract(remote.client, base["source"], item["archive_entry"])
            if digest(raw) != row["member_sha256"]:
                raise ValueError("FROZEN_MEMBER_HASH_MISMATCH: " + row["cik"])
            return issuer_diagnosis(
                json.loads(raw),
                row["cik"],
                row["member_sha256"],
                date_to=base["date_to"],
                as_of=base["maximum_as_of"],
            )

        with path.open("a") as handle, ThreadPoolExecutor(
            max_workers=args.period_workers
        ) as pool:
            for diagnosis in pool.map(diagnose, targets):
                handle.write(json.dumps(diagnosis, sort_keys=True) + "\n")
                handle.flush()
                period_rows[diagnosis["cik"]] = diagnosis
                print(
                    json.dumps(
                        {"diagnosed": diagnosis["cik"], "periods": len(diagnosis["periods"])}
                    ),
                    flush=True,
                )

    fail_closed_periods = all(row["fail_closed_every_period"] for row in period_rows.values())
    if not preservation["all_quarantines_match_policy_rule"]:
        raise ValueError("QUARANTINE_NOT_PRESERVED")
    if preservation["capex_fcf_debt_restrictions_released"]:
        raise ValueError("RESTRICTION_RELEASED_BY_THIS_WORK")
    if not scan["totals"]["fail_closed_every_issuer"]:
        raise ValueError("NOT_FAIL_CLOSED_IN_SERVED_COVERAGE")
    if not args.skip_periods and not fail_closed_periods:
        raise ValueError("NOT_FAIL_CLOSED_IN_EVERY_PERIOD")

    summary = [
        "# Statement-family gating diagnosis — full universe",
        "",
        f"Base release: {base['release_id']}",
        f"Issuers: {scan['totals']['issuers']}",
        f"Statement-family issuers: {scan['totals']['statement_family_issuers']}",
        f"Per-period diagnoses recorded: {len(period_rows)}",
        f"Remaining member bytes if incomplete: {planned_bytes}",
        f"Remote member reads this run: {remote.objects_read} objects / {remote.bytes_read} bytes",
        "Read-only: no policy, quarantine, registry or pointer value was written.",
        "",
        "## Families",
        "",
    ]
    summary += [
        f"- {family}: {count}" for family, count in sorted(scan["totals"]["families"].items())
    ]
    summary += ["", "## Classification evidence (concept: issuers)", ""]
    summary += [
        f"- {tag}: {count}"
        for tag, count in sorted(scan["totals"]["classification_evidence_counts"].items())
    ]
    summary += ["", "## Whole-entry quarantines", ""]
    summary += [
        f"- {reason}: {count}"
        for reason, count in sorted(scan["totals"]["entry_quarantine_reasons"].items())
    ]
    summary += [
        "",
        "## Fail-closed",
        "",
        f"- served-coverage leaks: {len(scan['totals']['fail_closed_violations'])}",
        f"- quarantines matching policy rule: {preservation['all_quarantines_match_policy_rule']}",
        f"- capex/FCF/debt restrictions released: "
        f"{preservation['capex_fcf_debt_restrictions_released'] or 'none'}",
        f"- per-period fail-closed: {'yes' if fail_closed_periods else 'NO/INCOMPLETE'}",
        "",
        "## Statement-family issuers (first 50; all rows in universe-period-gating.jsonl)",
        "",
        "| issuer | evidence concepts | periods | gated in every period |",
        "| --- | --- | --- | --- |",
    ]
    for cik in scan["totals"]["statement_family_ciks"][:50]:
        row = period_rows.get(cik)
        summary.append(
            "| {cik} | {evidence} | {periods} | {closed} |".format(
                cik=cik,
                evidence=", ".join(
                    next(r["classification_evidence"] for r in scan["issuers"] if r["cik"] == cik)
                )
                or "-",
                periods=len(row["periods"]) if row else "-",
                closed=("yes" if row["fail_closed_every_period"] else "NO") if row else "-",
            )
        )
    if GE in period_rows:
        summary += [
            "",
            "## GE across portfolio changes",
            "",
            "| fiscal year | lending rows | insurance rows | gating in effect |",
            "| --- | --- | --- | --- |",
        ]
        for year, row in sorted(ge_timeline(period_rows[GE]).items()):
            summary.append(
                f"| {year} | {row['lending_rows']} | {row['insurance_rows']} | "
                f"{'yes' if row['gating_in_effect'] else 'NO'} |"
            )
    summary += [
        "",
        "Gating is issuer-global: taxonomy evidence stopping in later periods does not",
        "release any period. Lifting the quarantine requires audited statements and a",
        "reviewed accounting policy; adding a concept to the registry is not a repair.",
        "",
    ]
    (OUT / "universe-summary.md").write_text("\n".join(summary))
    print(
        json.dumps(
            {
                "status": "PASS",
                "issuers": scan["totals"]["issuers"],
                "statement_family_issuers": scan["totals"]["statement_family_issuers"],
                "period_rows": len(period_rows),
                "fail_closed_every_issuer": True,
                "out": str(OUT),
            }
        )
    )


def run_diagnosed_issuers(remote, base):
    catalog = {}
    for suffix in sorted({cik[-2:] for cik in ISSUERS}):
        catalog[suffix] = json.loads(remote.get(base["shards"][suffix]))

    diagnosies = {}
    member_shas = {}
    for cik in ISSUERS:
        item = catalog[cik[-2:]].get(cik)
        if item is None:
            raise ValueError("CANDIDATE_SCOPE_CATALOG_ENTRY_MISSING: " + cik)
        cache = CANDIDATE_OUT / "raw" / cik / (item["materialized"]["member_sha256"] + ".json")
        if cache.exists():
            raw = cache.read_bytes()
        else:
            raw = extract(remote.client, base["source"], item["archive_entry"])
            write_once(cache, raw)
        if digest(raw) != item["materialized"]["member_sha256"]:
            raise ValueError("FROZEN_MEMBER_HASH_MISMATCH: " + cik)
        member_shas[cik] = item["materialized"]["member_sha256"]
        company = json.loads(raw)
        diagnosies[cik] = issuer_diagnosis(
            company,
            cik,
            item["materialized"]["member_sha256"],
            date_to=base["date_to"],
            as_of=base["maximum_as_of"],
        )

    report = {
        "base_release_id": base["release_id"],
        "source_member_sha256": member_shas,
        "date_to": base["date_to"],
        "maximum_as_of": base["maximum_as_of"],
        "read_only": True,
        "policy_changes_written": 0,
        "issuers": diagnosies,
    }
    ge = ge_timeline(diagnosies[GE])
    preservation = quarantine_preservation(diagnosies)
    if not preservation["policy_unchanged_by_this_work"]:
        raise ValueError("QUARANTINE_NOT_PRESERVED")
    if not all(d["fail_closed_every_period"] for d in diagnosies.values()):
        raise ValueError("NOT_FAIL_CLOSED_IN_EVERY_PERIOD")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "issuer-period-gating.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (OUT / "ge-portfolio-timeline.json").write_text(json.dumps(ge, indent=2, sort_keys=True) + "\n")
    (OUT / "quarantine-preservation.json").write_text(
        json.dumps(preservation, indent=2, sort_keys=True) + "\n"
    )

    summary = [
        "# Statement-family gating diagnosis (issuer x observation period)",
        "",
        f"Base release: {base['release_id']}",
        f"Observation window: 1930-01-01 .. {base['date_to']} (as_of {base['maximum_as_of']})",
        "Read-only: no policy, quarantine, registry or pointer value was written.",
        "",
        "## Fail-closed summary",
        "",
        "| issuer | family | evidence concepts | periods | gated in every period |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cik, diagnosis in sorted(diagnosies.items()):
        summary.append(
            "| {cik} | {family} | {evidence} | {periods} | {closed} |".format(
                cik=cik,
                family=diagnosis["family"],
                evidence=", ".join(diagnosis["classification_evidence"]) or "-",
                periods=len(diagnosis["periods"]),
                closed="yes" if diagnosis["fail_closed_every_period"] else "NO",
            )
        )
    summary += [
        "",
        "## GE across portfolio changes",
        "",
        "| fiscal year | lending rows | insurance rows | gating in effect |",
        "| --- | --- | --- | --- |",
    ]
    for year, row in sorted(ge.items()):
        summary.append(
            f"| {year} | {row['lending_rows']} | {row['insurance_rows']} | "
            f"{'yes' if row['gating_in_effect'] else 'NO'} |"
        )
    summary += [
        "",
        "Gating is issuer-global: taxonomy evidence stopping in later periods does not",
        "release any period. Lifting the quarantine requires audited statements and a",
        "reviewed accounting policy; adding a concept to the registry is not a repair.",
        "The capex/FCF/debt restrictions remain in force (quarantine-preservation.json).",
        "",
    ]
    (OUT / "diagnosis-summary.md").write_text("\n".join(summary))
    print(
        json.dumps(
            {
                "status": "PASS",
                "issuers": sorted(diagnosies),
                "fail_closed_every_period": True,
                "policy_unchanged_by_this_work": True,
                "out": str(OUT),
            }
        )
    )


def main():
    from reconcile_financial_inventory import client

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--universe",
        action="store_true",
        help="diagnose every issuer in the deployed catalog, not only the six",
    )
    parser.add_argument(
        "--skip-periods",
        action="store_true",
        help="universe scan only; skip raw-member per-period depth",
    )
    parser.add_argument("--period-workers", type=int, default=8)
    args = parser.parse_args()

    config = json.loads((ROOT / "deploy/cloudflare-query/thin/wrangler.jsonc").read_text())["vars"]
    remote = Remote(client())
    response = remote.client.get_object(Bucket=BUCKET, Key=config["SEC_ENGINE_MANIFEST_KEY"])
    try:
        base_body = response["Body"].read(1048577)
    finally:
        response["Body"].close()
    if digest(base_body) != config["SEC_ENGINE_MANIFEST_SHA256"]:
        raise ValueError("BASE_MANIFEST_PIN_MISMATCH")
    base = json.loads(base_body)

    if args.universe:
        run_universe(remote, base, args)
    else:
        run_diagnosed_issuers(remote, base)


if __name__ == "__main__":
    main()
