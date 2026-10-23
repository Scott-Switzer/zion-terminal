"""Build the affected-issuer engine-release candidate for the WMT revenue pin.

Read-only against the preserved deployment release and the frozen 2026-09-24
archive member; writes a local, unpromoted candidate under
``artifacts/path1-revenue-override-candidate/``. Nothing is uploaded, no
pointer or configuration value is written, and the base release objects stay
untouched.

The candidate mirrors the production update-release shape
(``sec_publish_update_release.publish_update``): unchanged issuers are
inherited byte-identically by pinned reference, and only the affected issuer
(Walmart, CIK 0000104169) is rebuilt — with these gates:

1. the frozen member must reproduce the deployed WMT company object
   byte-for-byte before the pin is applied;
2. the pinned rebuild must change only the revenue definition: WMT's annual
   revenue must equal the 10-K "Total revenues" line exactly, no observation
   may appear or disappear outside the two unpinned revenue concepts, and
   every non-revenue metric must reproduce the deployed coverage counts;
3. every unaffected issuer's company-object reference and catalog entry must
   hash byte-identically between the base and the candidate (compared for all
   issuers, not samples).

Run:  python3.12 scripts/build_revenue_override_candidate.py
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))
sys.path.insert(0, str(Path(__file__).parent))

from materialize_sec_engine import extract, materialize  # noqa: E402
from sec_engine import REGISTRY, encoded, history  # noqa: E402
from sec_publish_update_release import chunks, ordered_rows  # noqa: E402
from sec_screener import company_row  # noqa: E402

OUT = Path(__file__).resolve().parents[3] / "artifacts/path1-revenue-override-candidate"
WMT = "0000104169"
CHANGE_CLASS = "REGISTRY_ISSUER_REVENUE_OVERRIDE"
BUCKET = "financial-system-datasets"
# Verified against the 10-K "Total revenues" line (SEC companyconcept, us-gaap:Revenues).
EXPECTED_TOTAL_REVENUES_LINE = {
    "2024-01-31": "648125000000",
    "2025-01-31": "680985000000",
    "2026-01-31": "713163000000",
}
DROPPED_REVENUE_CONCEPTS = {
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:SalesRevenueNet",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_once(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != body:
        raise ValueError("LOCAL_IMMUTABLE_CONFLICT: " + str(path))
    path.write_bytes(body)


class Remote:
    """Bounded read-only object access; every read is verified against its ref."""

    def __init__(self, client):
        self.client = client
        self.bytes_read = 0
        self.objects_read = 0

    def get(self, ref, limit=64 * 1048576):
        if not 0 <= ref["bytes"] <= limit:
            raise ValueError("READ_BUDGET")
        response = self.client.get_object(Bucket=BUCKET, Key=ref["key"])
        try:
            raw = response["Body"].read(ref["bytes"] + 1)
        finally:
            response["Body"].close()
        if len(raw) != ref["bytes"] or digest(raw) != ref["sha256"]:
            raise ValueError("ARTIFACT_HASH_MISMATCH: " + ref["key"])
        self.bytes_read += len(raw)
        self.objects_read += 1
        return raw


def pin(prefix, name, body):
    return {"key": prefix + name, "sha256": digest(body), "bytes": len(body)}


def wmt_gap_analysis(company, results):
    """Explain every remaining annual revenue gap from the raw concept rows.

    A pinned concept does not resolve conflicts inside the concept itself: when
    Walmart refiled unequal values for the same period (or for a period whose
    derivations reach this window), the engine keeps its ambiguity rule and
    refuses to choose. Each gap lists the conflicting raw rows so the refusal
    is auditable.
    """
    rows = company["facts"]["us-gaap"]["Revenues"]["units"]["USD"]

    def raw(row):
        return {
            "start": row.get("start"),
            "end": row.get("end"),
            "val": str(row.get("val")),
            "filed": row.get("filed"),
            "form": row.get("form"),
            "fy": row.get("fy"),
            "fp": row.get("fp"),
        }

    gaps = []
    for gap in results["annual"]["coverage_gaps"]:
        if gap["metric"] != "revenue":
            continue
        window = [
            raw(row)
            for row in rows
            if row.get("start") == gap["period_start"] and row.get("end") == gap["period_end"]
        ]
        same_end = [raw(row) for row in rows if row.get("end") == gap["period_end"]]
        gaps.append(
            {
                "period_start": gap["period_start"],
                "period_end": gap["period_end"],
                "reason": gap["reason"],
                "exact_window_rows": window,
                "distinct_window_values": sorted({row["val"] for row in window}),
                "rows_sharing_period_end": same_end,
                "distinct_values_at_period_end": sorted({row["val"] for row in same_end}),
            }
        )
        if (
            len(gaps[-1]["distinct_window_values"]) < 2
            and len(gaps[-1]["distinct_values_at_period_end"]) < 2
        ):
            raise ValueError("GAP_NOT_EXPLAINED_BY_UNEQUAL_RESTATEMENTS: " + gap["period_end"])
    return {
        "status": "PASS",
        "check": (
            "the issuer pin selects the concept; it does not resolve within-concept "
            "restatement conflicts, which stay fail-closed gaps"
        ),
        "annual_revenue_gaps": gaps,
        "served_annual_periods": sum(
            1 for row in results["annual"]["observations"] if row["metric"] == "revenue"
        ),
    }


def coverage_counts(data, manifest):
    counts = {}
    results = {}
    for frequency in ("annual", "quarterly", "ttm"):
        result = history(
            data,
            metrics=list(REGISTRY),
            frequency=frequency,
            date_from="1930-01-01",
            date_to=manifest["date_to"],
            as_of=manifest["maximum_as_of"],
        )
        results[frequency] = result
        counts[frequency] = {
            metric: {
                "observations": sum(r["metric"] == metric for r in result["observations"]),
                "gaps": sum(g["metric"] == metric for g in result["coverage_gaps"]),
            }
            for metric in REGISTRY
        }
    return counts, results


def main():
    from reconcile_financial_inventory import client

    config = json.loads((ROOT / "deploy/cloudflare-query/thin/wrangler.jsonc").read_text())[
        "vars"
    ]
    remote = Remote(client())
    manifest_ref = {
        "key": config["SEC_ENGINE_MANIFEST_KEY"],
        "sha256": config["SEC_ENGINE_MANIFEST_SHA256"],
    }
    response = remote.client.get_object(Bucket=BUCKET, Key=manifest_ref["key"])
    try:
        base_body = response["Body"].read(1048577)
    finally:
        response["Body"].close()
    if digest(base_body) != manifest_ref["sha256"]:
        raise ValueError("BASE_MANIFEST_PIN_MISMATCH")
    manifest_ref["bytes"] = len(base_body)
    remote.bytes_read += len(base_body)
    remote.objects_read += 1
    base = json.loads(base_body)
    if base.get("rights") != "PUBLIC_SEC_ONLY" or base.get("licensed_sources_allowed") is not False:
        raise ValueError("PUBLIC_SEC_ONLY_REQUIRED")
    base_registry_body = remote.get(base["registry"])
    base_registry = json.loads(base_registry_body)
    if digest(base_registry_body) != base["registry_sha256"]:
        raise ValueError("BASE_REGISTRY_IDENTITY_MISMATCH")

    core = ROOT / "deploy/cloudflare-query/src"
    registry = {
        "version": base_registry["version"],
        "metrics": REGISTRY,
        "implementation_sha256": digest((core / "sec_engine.py").read_bytes()),
        "policy": base_registry["policy"],
        "dependency_sha256": {
            name: digest((core / name).read_bytes())
            for name in sorted(base_registry["dependency_sha256"])
        },
        "publication_schema_version": base_registry["publication_schema_version"],
    }
    if registry["metrics"] != base_registry["metrics"]:
        raise ValueError("REGISTRY_METRICS_CHANGED_BY_THIS_BUILD")
    if registry["dependency_sha256"] != base_registry["dependency_sha256"]:
        raise ValueError("REGISTRY_DEPENDENT_IMPLEMENTATION_DRIFT")
    changed_fields = sorted(k for k in registry if registry[k] != base_registry.get(k))
    if changed_fields != ["implementation_sha256"]:
        raise ValueError("REGISTRY_DELTA_NOT_IMPLEMENTATION_ONLY: " + str(changed_fields))
    registry_body = encoded(registry)
    new_registry_sha = digest(registry_body)

    release = digest(
        encoded(
            {
                "parent_manifest_sha256": digest(base_body),
                "change_class": CHANGE_CLASS,
                "registry_sha256": new_registry_sha,
                "implementation_sha256": registry["implementation_sha256"],
                "affected_ciks": [WMT],
            }
        )
    )
    prefix = "gold/sec-engine/releases/" + release + "/"

    catalog = {
        suffix: json.loads(remote.get(base["shards"][suffix]))
        for suffix in sorted(base["shards"])
    }
    if WMT not in catalog[WMT[-2:]]:
        raise ValueError("WMT_CATALOG_ENTRY_MISSING")
    base_item = catalog[WMT[-2:]][WMT]
    entry = base_item["archive_entry"]

    cache = OUT / "raw" / WMT / (base_item["materialized"]["member_sha256"] + ".json")
    if cache.exists():
        raw = cache.read_bytes()
    else:
        raw = extract(remote.client, base["source"], entry)
        write_once(cache, raw)
    if digest(raw) != base_item["materialized"]["member_sha256"]:
        raise ValueError("FROZEN_MEMBER_HASH_MISMATCH")
    company = json.loads(raw)

    prior_ref = base_item["materialized"]
    prior_body = remote.get(prior_ref)
    prior = json.loads(prior_body)

    # Gate 1: without the issuer pin (the pre-pin registry), the frozen member
    # must reproduce the deployed object exactly, proving the pin is the only
    # delta between the deployed object and the candidate object.
    import sec_engine

    pinned = sec_engine.REVENUE_CONCEPTS.pop(WMT, None)
    try:
        reproduced = materialize(
            raw, WMT, base["source"], entry, prior["release_id"], prior["registry_sha256"]
        )
    finally:
        if pinned is not None:
            sec_engine.REVENUE_CONCEPTS[WMT] = pinned
    if encoded(reproduced) != prior_body:
        raise ValueError("DEPLOYED_OBJECT_REPRODUCTION_FAILED")

    # Gate 2: the pinned rebuild changes only the revenue definition.
    data = materialize(raw, WMT, base["source"], entry, release, new_registry_sha)
    counts, results = coverage_counts(data, base)
    observed = {
        row["period_end"]: row["value_decimal"]
        for row in results["annual"]["observations"]
        if row["metric"] == "revenue"
    }
    headline = {k: observed.get(k) for k in EXPECTED_TOTAL_REVENUES_LINE}
    if headline != EXPECTED_TOTAL_REVENUES_LINE:
        raise ValueError("WMT_TOTAL_REVENUE_MISMATCH: " + json.dumps(headline, sort_keys=True))
    if len(observed) != 19:
        raise ValueError("WMT_ANNUAL_REVENUE_SERIES_LENGTH: " + str(len(observed)))
    concepts = {
        row["concept"] for row in results["annual"]["observations"] if row["metric"] == "revenue"
    }
    if concepts != {"us-gaap:Revenues"}:
        raise ValueError("WMT_REVENUE_CONCEPT_MISMATCH: " + str(concepts))
    for metric in REGISTRY:
        if metric == "revenue":
            continue
        for frequency in counts:
            if counts[frequency][metric] != base_item["coverage"][frequency][metric]:
                raise ValueError("NON_REVENUE_COVERAGE_CHANGED: " + metric + "/" + frequency)
    if data["policy"]["revenue_concepts"] != ["Revenues"]:
        raise ValueError("WMT_POLICY_REVENUE_CONCEPTS")
    if data["policy"] != {**prior["policy"], "revenue_concepts": ["Revenues"]}:
        raise ValueError("WMT_POLICY_DELTA_NOT_REVENUE_CONCEPTS_ONLY")
    prior_rows = {r["source_observation_id"]: r for r in prior["observations"]}
    new_rows = {r["source_observation_id"]: r for r in data["observations"]}
    added = sorted(set(new_rows) - set(prior_rows))
    removed = [prior_rows[sid] for sid in sorted(set(prior_rows) - set(new_rows))]
    if added:
        raise ValueError("OBSERVATIONS_ADDED_BY_THE_PIN: " + str(added[:3]))
    if any(r["metric"] != "revenue" or r["concept"] not in DROPPED_REVENUE_CONCEPTS for r in removed):
        raise ValueError("OBSERVATIONS_REMOVED_OUTSIDE_UNPINNED_REVENUE_CONCEPTS")
    for sid, row in new_rows.items():
        if row != prior_rows[sid]:
            raise ValueError("OBSERVATION_REVISIOND_BY_THE_PIN: " + sid)
    for key in set(data) | set(prior):
        if key in (
            "release_id",
            "registry_sha256",
            "policy",
            "observations",
            "rejected_contexts",
            "rejections",
            "concept_definitions",
        ):
            continue
        if encoded(data.get(key)) != encoded(prior.get(key)):
            raise ValueError("UNEXPECTED_OBJECT_FIELD_CHANGED: " + key)
    rejection_delta = {
        reason: data.get("rejections", {}).get(reason, 0) - count
        for reason, count in prior.get("rejections", {}).items()
        if data.get("rejections", {}).get(reason, 0) != count
    }
    if any(v > 0 for v in rejection_delta.values()) or set(data.get("rejections", {})) - set(
        prior.get("rejections", {})
    ):
        raise ValueError("REJECTIONS_INCREASED_BY_THE_PIN: " + json.dumps(rejection_delta))
    if data.get("rejected_contexts") != prior.get("rejected_contexts"):
        raise ValueError("REJECTED_CONTEXTS_CHANGED_BY_THE_PIN")
    dropped_definitions = {
        k: v for k, v in prior.get("concept_definitions", {}).items()
        if data.get("concept_definitions", {}).get(k) != v
    }
    if any(not k.endswith(("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"))
           for k in dropped_definitions):
        raise ValueError("CONCEPT_DEFINITIONS_CHANGED_OUTSIDE_UNPINNED_CONCEPTS")

    company_body = encoded(data)
    write_once(OUT / "companies" / (WMT + ".json"), company_body)
    write_once(OUT / "registry.json", registry_body)

    new_item = dict(base_item)
    new_item["materialized"] = {
        "key": prefix + "companies/" + WMT + ".json",
        "sha256": digest(company_body),
        "bytes": len(company_body),
        "member_sha256": data["member_sha256"],
        "source_release_id": release,
    }
    new_item["policy"] = data["policy"]
    new_item["coverage"] = counts
    changed_shard = dict(catalog[WMT[-2:]])
    changed_shard[WMT] = new_item
    shard_body = encoded(changed_shard)
    write_once(OUT / "catalog" / (WMT[-2:] + ".json"), shard_body)

    # Screen shards: keep every unaffected row, replace WMT's row, restamp wrappers.
    full_row = company_row(WMT, data, results["annual"]["observations"])
    new_screen = {}
    screen_inputs = base["screening_metrics"]["metrics"]
    for metric in sorted(screen_inputs):
        rows = []
        for ref in screen_inputs[metric]:
            value = json.loads(remote.get(ref))
            if (
                value["release_id"] != base["release_id"]
                or value["registry_sha256"] != base["registry_sha256"]
            ):
                raise ValueError("SCREEN_CONTEXT_MISMATCH: " + ref["key"])
            rows.extend(row for row in value["companies"] if row["cik"] != WMT)
        rows.append(
            {
                **{k: v for k, v in full_row.items() if k not in ("metrics", "missing")},
                "metrics": {metric: full_row["metrics"][metric]}
                if metric in full_row["metrics"]
                else {},
                "missing": {metric: full_row["missing"].get(metric)},
            }
        )
        if len({r["cik"] for r in rows}) != len(rows):
            raise ValueError("DUPLICATE_SCREEN_COMPANY")
        refs = []
        for index, part in enumerate(chunks(ordered_rows(rows, metric))):
            body = encoded(
                dict(companies=part, release_id=release, registry_sha256=new_registry_sha)
            )
            write_once(OUT / "screen" / metric / (str(index) + ".json"), body)
            refs.append(pin(prefix, "screen/" + metric + "/" + str(index) + ".json", body))
        new_screen[metric] = refs

    new = dict(base)
    new["release_id"] = release
    new["registry_sha256"] = new_registry_sha
    new["registry"] = pin(prefix, "registry.json", registry_body)
    new["shards"] = dict(base["shards"])
    new["shards"][WMT[-2:]] = pin(prefix, "catalog/" + WMT[-2:] + ".json", shard_body)
    new["screening_metrics"] = dict(base["screening_metrics"], metrics=new_screen)
    new["source_releases"] = sorted(set(base.get("source_releases", [])) | {release})
    new["previous_releases"] = {
        **base.get("previous_releases", {}),
        base["release_id"]: {
            "key": manifest_ref["key"],
            "sha256": manifest_ref["sha256"],
            "bytes": manifest_ref["bytes"],
        },
    }
    new["registry_change"] = {
        "change_class": CHANGE_CLASS,
        "affected_ciks": [WMT],
        "registry_sha256_from": base["registry_sha256"],
        "registry_sha256_to": new_registry_sha,
        "implementation_sha256_from": base_registry["implementation_sha256"],
        "implementation_sha256_to": registry["implementation_sha256"],
        "unaffected_payloads": "INHERITED_BYTE_IDENTICAL_BY_PINNED_REFERENCE",
        "justification": (
            "Walmart reports total revenues (us-gaap:Revenues) and ASC-606 revenue with "
            "different values for the same annual periods, so every period is ambiguous "
            "under the default concept list. The issuer pin selects total revenues for "
            "Walmart only; the collision rule and every other issuer are unchanged."
        ),
        "promotion_status": "UNAPPROVED_CANDIDATE",
        "production_pointer_writes": 0,
    }
    new_body = encoded(new)
    root_ref = pin(prefix, "manifest.json", new_body)
    write_once(OUT / "manifest.json", new_body)

    # Gate 3 + exact accounting: every issuer's object reference and catalog entry.
    companies_report = []
    catalog_report = []
    for suffix in sorted(catalog):
        base_rows = catalog[suffix]
        new_rows_map = changed_shard if suffix == WMT[-2:] else catalog[suffix]
        for cik in sorted(base_rows):
            base_entry, new_entry = base_rows[cik], new_rows_map[cik]
            base_object = base_entry.get("materialized") or {}
            new_object = new_entry.get("materialized") or {}
            entry_equal = encoded(base_entry) == encoded(new_entry)
            object_equal = (
                (base_object.get("sha256"), base_object.get("bytes"), base_object.get("key"))
                == (new_object.get("sha256"), new_object.get("bytes"), new_object.get("key"))
            )
            if cik != WMT and not (entry_equal and object_equal):
                raise ValueError("UNAFFECTED_ISSUER_CHANGED: " + cik)
            companies_report.append(
                {
                    "cik": cik,
                    "affected": cik == WMT,
                    "object_sha256_base": base_object.get("sha256"),
                    "object_sha256_candidate": new_object.get("sha256"),
                    "object_reference_equal": object_equal,
                }
            )
            catalog_report.append(
                {
                    "cik": cik,
                    "shard": suffix,
                    "entry_sha256_base": digest(encoded(base_entry)),
                    "entry_sha256_candidate": digest(encoded(new_entry)),
                    "entry_equal": entry_equal,
                }
            )
    shard_report = [
        {
            "shard": suffix,
            "sha256_base": base["shards"][suffix]["sha256"],
            "sha256_candidate": new["shards"][suffix]["sha256"],
            "inherited_byte_identical": new["shards"][suffix] == base["shards"][suffix],
        }
        for suffix in sorted(base["shards"])
    ]
    changed_manifest_keys = sorted(
        k for k in set(new) | set(base) if encoded(new.get(k)) != encoded(base.get(k))
    )
    unaffected = [row for row in companies_report if not row["affected"]]

    write_once(
        OUT / "verification/reproduction.json",
        encoded(
            {
                "status": "PASS",
                "check": (
                    "frozen member reproduces the deployed WMT company object "
                    "byte-for-byte before the pin is applied"
                ),
                "member_sha256": data["member_sha256"],
                "prior_release_id": prior["release_id"],
                "prior_object_sha256": digest(prior_body),
            }
        ),
    )
    write_once(
        OUT / "verification/wmt-semantics.json",
        encoded(
            {
                "status": "PASS",
                "annual_revenue_observations": len(observed),
                "annual_revenue": observed,
                "revenue_concepts_used": sorted(concepts),
                "expected_total_revenues_line": EXPECTED_TOTAL_REVENUES_LINE,
                "policy_revenue_concepts": data["policy"]["revenue_concepts"],
                "coverage_before": base_item["coverage"],
                "coverage_after": counts,
                "observations_removed_by_the_pin": len(removed),
                "observations_removed_concepts": sorted({r["concept"] for r in removed}),
                "observations_added_by_the_pin": 0,
                "non_revenue_metrics_changed": [],
                "rejection_delta": rejection_delta,
                "ambiguity_rule_for_other_issuers": (
                    "UNCHANGED; see tests/unit/test_revenue_coverage_diagnosis.py"
                ),
            }
        ),
    )
    write_once(
        OUT / "verification/wmt-revenue-gap-analysis.json",
        encoded(wmt_gap_analysis(company, results)),
    )
    write_once(
        OUT / "verification/unaffected-company-objects.json",
        encoded(
            {
                "compared_issuers": len(companies_report),
                "affected_issuers": [WMT],
                "unaffected_issuers_compared": len(unaffected),
                "all_unaffected_references_equal": all(
                    row["object_reference_equal"] for row in unaffected
                ),
                "issuers": companies_report,
            }
        ),
    )
    write_once(
        OUT / "verification/unaffected-catalog-entries.json",
        encoded(
            {
                "compared_entries": len(catalog_report),
                "all_unaffected_entries_equal": all(
                    row["entry_equal"] for row in catalog_report if row["cik"] != WMT
                ),
                "shards": shard_report,
                "entries": catalog_report,
            }
        ),
    )

    def name_of(key):
        return key[len(prefix):] if key.startswith(prefix) else key

    affected = [
        {"name": name_of(root_ref["key"]), "sha256": root_ref["sha256"], "bytes": root_ref["bytes"]},
        {"name": "registry.json", "sha256": new["registry"]["sha256"], "bytes": new["registry"]["bytes"]},
        {"name": "companies/" + WMT + ".json", "sha256": digest(company_body), "bytes": len(company_body)},
        {"name": "catalog/" + WMT[-2:] + ".json", "sha256": digest(shard_body), "bytes": len(shard_body)},
    ]
    for metric in sorted(new_screen):
        for ref in new_screen[metric]:
            affected.append(
                {"name": name_of(ref["key"]), "sha256": ref["sha256"], "bytes": ref["bytes"]}
            )
    write_once(
        OUT / "affected-objects.json",
        encoded(
            {
                "change_class": CHANGE_CLASS,
                "candidate_release_id": release,
                "parent_release_id": base["release_id"],
                "objects_written": len(affected),
                "objects_written_bytes": sum(row["bytes"] for row in affected),
                "inherited_payloads": {
                    "catalog_shards_byte_identical": sum(
                        1 for row in shard_report if row["inherited_byte_identical"]
                    ),
                    "company_objects_byte_identical": len(unaffected),
                    "directory_inherited": new["directory"] == base["directory"],
                },
                "manifest_keys_changed": changed_manifest_keys,
                "justification": {
                    "companies/" + WMT + ".json": (
                        "revenue issuer pin applied to the affected issuer only"
                    ),
                    "catalog/" + WMT[-2:] + ".json": (
                        "WMT catalog entry carries the rebuilt object reference, policy and coverage"
                    ),
                    "registry.json": (
                        "implementation_sha256 follows the pinned sec_engine.py; metrics unchanged"
                    ),
                    "screen/*.json": (
                        "derived screen rows carry release/registry stamps and WMT's revenue row"
                    ),
                    "manifest.json": "new release identity over inherited immutable payloads",
                },
                "objects": affected,
            }
        ),
    )
    write_once(
        OUT / "rollback-receipt.json",
        encoded(
            {
                "status": "PREPARED_NOT_APPLIED",
                "rollback": (
                    "Nothing was promoted: the candidate is local-only. On an approved rollout "
                    "the rollback is to keep SEC_ENGINE_MANIFEST_KEY/SEC_ENGINE_MANIFEST_SHA256 "
                    "pinned at the parent manifest and redeploy the prior worker version; the "
                    "parent release and all inherited objects remain immutable and untouched."
                ),
                "parent_release_id": base["release_id"],
                "parent_manifest": manifest_ref,
                "thin_config_pins_before": {
                    "SEC_ENGINE_MANIFEST_KEY": config["SEC_ENGINE_MANIFEST_KEY"],
                    "SEC_ENGINE_MANIFEST_SHA256": config["SEC_ENGINE_MANIFEST_SHA256"],
                },
                "candidate_release_id": release,
                "candidate_manifest": root_ref,
                "production_pointer_writes": 0,
                "configuration_writes": 0,
            }
        ),
    )
    write_once(
        OUT / "build-receipt.json",
        encoded(
            {
                "status": "PASS",
                "candidate_release_id": release,
                "parent_release_id": base["release_id"],
                "promotion_status": "UNAPPROVED_CANDIDATE",
                "production_pointer_writes": 0,
                "source_releases_unchanged": True,
                "frozen_archive": base["source"],
                "remote_objects_read": remote.objects_read,
                "remote_bytes_read": remote.bytes_read,
                "affected_issuers": [WMT],
                "unaffected_issuers_compared": len(unaffected),
                "catalog_entries_compared": len(catalog_report),
            }
        ),
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "candidate_release_id": release,
                "parent_release_id": base["release_id"],
                "registry_sha256": new_registry_sha,
                "objects_written": len(affected),
                "unaffected_issuers_compared": len(unaffected),
                "catalog_entries_compared": len(catalog_report),
            }
        )
    )


if __name__ == "__main__":
    main()
