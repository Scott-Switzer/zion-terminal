"""Isolated public-only Iceberg verification, never a runtime credential expansion."""
# ruff: noqa: E501

import json

import pyarrow as pa
from extract_sec_history_sources import OUT
from publish_basin_public_pilot import ACCOUNT, BUCKET, sql, token
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.io.pyarrow import _pyarrow_to_schema_without_ids


def main():
    manifest = json.loads((OUT / "history-manifest.json").read_text())
    namespace = "zion_sec_hist_" + manifest["release_id"][:12]
    cat = load_catalog(
        "r2",
        type="rest",
        uri=f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}",
        warehouse=f"{ACCOUNT}_{BUCKET}",
        token=token(),
    )
    try:
        cat.create_namespace(namespace)
    except NamespaceAlreadyExistsError:
        pass
    facts = []
    issuers = []
    for cik, ref in manifest["companies"].items():
        data = json.loads((OUT / (cik + ".history.json")).read_text())
        assert data["rights"] == "PUBLIC_SEC_ONLY"
        facts.extend(
            {
                k: r[k]
                for k in [
                    "cik",
                    "metric",
                    "concept",
                    "period_start",
                    "period_end",
                    "value_decimal",
                    "unit",
                    "available_at",
                    "source_observation_id",
                ]
            }
            for r in data["observations"]
        )
        issuers.append({"cik": cik, "label": ref["label"], "member_sha256": ref["member_sha256"]})
    pins = []
    for name, rows in [("public_sec_facts", facts), ("public_issuers", issuers)]:
        arrow = pa.table({k: pa.array([r.get(k) for r in rows], type=pa.string()) for k in rows[0]})
        try:
            table = cat.load_table((namespace, name))
        except NoSuchTableError:
            table = cat.create_table(
                (namespace, name), schema=_pyarrow_to_schema_without_ids(arrow.schema)
            )
        if table.current_snapshot() is None:
            table.append(
                arrow,
                snapshot_properties={
                    "rights": "PUBLIC_SEC_ONLY",
                    "source_release_id": manifest["release_id"],
                    "canonical_promotion": "FALSE",
                },
            )
        table.refresh()
        snapshot = table.current_snapshot()
        assert snapshot.summary.model_dump()["source_release_id"] == manifest["release_id"]
        pins.append(
            {
                "table": namespace + "." + name,
                "snapshot_id": str(snapshot.snapshot_id),
                "metadata_location": table.metadata_location,
                "bytes": int(snapshot.summary.model_dump()["total-files-size"]),
            }
        )
    checks = []
    for cik in manifest["companies"]:
        # Compare every normalized revision, not only aggregates or latest values.
        query = f"SELECT f.source_observation_id, f.value_decimal FROM {namespace}.public_sec_facts f JOIN {namespace}.public_issuers i ON f.cik=i.cik WHERE i.cik='{cik}' ORDER BY f.source_observation_id LIMIT 5001"
        result, latency = sql(query, token())
        expected = sorted(
            [(r["source_observation_id"], r["value_decimal"]) for r in facts if r["cik"] == cik]
        )
        actual = [(r["source_observation_id"], r["value_decimal"]) for r in result["rows"]]
        assert expected == actual
        checks.append(
            {
                "cik": cik,
                "status": "PASS",
                "source_revisions": len(actual),
                "latency_ms": latency,
                "metrics": result["metrics"],
            }
        )
    evidence = {
        "status": "PASS",
        "namespace": namespace,
        "source_release_id": manifest["release_id"],
        "checks": checks,
        "tables": pins,
        "licensed_migrations": 0,
        "production_pointer_changes": 0,
        "runtime_read_token_validated": False,
        "credential_usage": "EXISTING_OPERATOR_ONLY;NOT_INSTALLED_FOR_NEW_HISTORY_API",
    }
    (OUT / "basin-history-verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
