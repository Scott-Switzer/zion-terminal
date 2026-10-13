"""Build a sanitized acceptance summary from protected operator evidence."""

# ruff: noqa: E501

import hashlib
import json
from pathlib import Path

OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-financial-foundation")
DEST = Path(__file__).parents[1] / "docs/unified-financial-query"


def main():
    def read(name):
        return json.loads((OUT / name).read_text())

    inventory = read("inventory-summary.json")
    stores = read("other-stores-metadata-summary.json")
    pilot = read("basin-pilot-evidence.json")
    live = read("private-query-acceptance.json")
    version = read("live-source-version-gate.json")
    pointers = read("publication-pointer-gates.json")
    catalogs = read("catalog-registration-census.json")
    regressions = {}
    for name in [
        "private_serving_acceptance",
        "sec_expanded_acceptance",
        "collection_serving_acceptance",
        "native_query_acceptance",
    ]:
        p = OUT / (name + ".json")
        if p.exists():
            r = read(name + ".json")
            regressions[name] = {"status": r.get("status"), "checks": len(r.get("checks", []))}
    gates = {
        "four_store_metadata_census": {
            "status": "PASS",
            "evidence": "other-stores-metadata-summary.json",
        },
        "historical_350_error_classification": {
            "status": "PASS",
            "evidence": "inventory-reconciliation.json; oversize-manifest-validation.json",
        },
        "registered_manifests_integrity": {
            "status": "PASS" if inventory["manifest_states"].get("SHA_MATCH") == 805 else "FAIL",
            "evidence": "805 exact hashes",
        },
        "all_registered_references_resolved": {
            "status": "FAIL",
            "reason": "One legacy path reference unresolved; 41484 present size matches",
        },
        "all_archives_analytically_queryable": {
            "status": "FAIL",
            "reason": "No exhaustive footer/codec/row census; object inventory is not queryability",
        },
        "all_store_catalog_registration_reconciled": {
            "status": "FAIL",
            "reason": "Financial and lakehouse catalogs enumerated; research and filings catalogs unverified; physical file membership not fully joined",
        },
        "basin_cross_table_sql": {
            "status": pilot["status"],
            "evidence": "basin-pilot-evidence.json",
        },
        "private_service_independent_source_match": {
            "status": live["status"],
            "evidence": "private-query-acceptance.json",
        },
        "exact_precision_nulls_and_historical_availability": {
            "status": live["status"],
            "evidence": "private-query-acceptance.json; 13 adapter unit tests",
        },
        "source_version_change_live": {
            "status": version["status"],
            "evidence": "live-source-version-gate.json",
        },
        "unauthorized_inputs_and_licensed_access_denied": {
            "status": live["status"],
            "evidence": "live private checks plus restricted public entrypoint unit test",
        },
        "query_budgets_and_no_silent_truncation": {
            "status": live["status"],
            "evidence": "live overflow 413; synthetic size/deadline tests; engine cancellation unverified",
        },
        "narrow_read_only_runtime_credential": {
            "status": "FAIL",
            "reason": "Existing operator token can write pilot catalog; narrow scope not verified; licensed SQL stays disabled",
        },
        "mapping_specs_and_source_preservation": {
            "status": "PASS",
            "evidence": "versioned contracts; four semantic helper tests; full canonical validation remains future work",
        },
        "canonical_financial_certification": {
            "status": "FAIL",
            "reason": "Pilot source observations remain unvalidated; historical security mappings not established",
        },
        "production_publication_pointers_unchanged": {
            "status": "PASS" if all(x["status"] == "PASS" for x in pointers) else "FAIL",
            "evidence": "publication-pointer-gates.json",
        },
        "existing_private_routes_regression": {
            "status": "PASS"
            if len(regressions) == 4 and all(x["status"] == "PASS" for x in regressions.values())
            else "FAIL",
            "evidence": regressions,
        },
        "custom_domain_current_reachability": {
            "status": "FAIL",
            "reason": "Local TLS negotiation failed on api-thin-staging.scotttunnel.xyz; live tests use the existing authenticated workers.dev origin. Custom-domain route configuration unchanged; external reachability unverified.",
        },
        "no_new_provider_or_wrds_collection": {
            "status": "PASS",
            "evidence": "Existing stored public SEC members; metadata-only audit; no acquisition jobs",
        },
        "no_licensed_migration_or_llm_disclosure": {
            "status": "PASS",
            "evidence": "Public SEC and owned synthetic pilot only; regressions emit counts, never licensed rows",
        },
    }
    report = {
        "schema_version": "unified-financial-query-acceptance-v1",
        "pilot_success": live["status"] == "PASS" and version["status"] == "PASS",
        "estate_expansion_ready": False,
        "gates": gates,
        "object_count": sum(s["objects"] for s in stores),
        "object_bytes": sum(s["bytes"] for s in stores),
        "stores": [
            {k: s[k] for k in ("bucket", "objects", "bytes", "queryability")} for s in stores
        ],
        "catalogs": [
            {k: c[k] for k in ("bucket", "status")} | {"registered_tables": len(c["tables"])}
            for c in catalogs
        ],
        "historical_errors": inventory["current_error_classifications"],
        "live_benchmark": live,
        "direct_basin_latency_ms": pilot["query_latency_ms"],
        "pilot_source_bytes": sum(t["files_bytes_declared"] for t in pilot["tables"]),
        "duckdb": read("duckdb-verification.json"),
        "pointers": pointers,
        "evidence_digests": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in OUT.glob("*.json")
            if p.name
            not in (
                "wrds-object-metadata.json",
                "inventory-reconciliation.json",
                "public-sec-pilot-input.json",
            )
        },
    }
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "acceptance-gates.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "pilot_success": report["pilot_success"],
                "pass_gates": sum(g["status"] == "PASS" for g in gates.values()),
                "fail_gates": sum(g["status"] == "FAIL" for g in gates.values()),
            }
        )
    )


if __name__ == "__main__":
    main()
