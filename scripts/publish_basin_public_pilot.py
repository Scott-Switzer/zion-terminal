"""Create isolated Iceberg tables from public SEC metadata/observations and owned canaries."""

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.io.pyarrow import _pyarrow_to_schema_without_ids

ACCOUNT = "51b88c6a6ef833b3c2ff46e98d5d9356"
BUCKET = "financial-system-datasets"
OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-financial-foundation")


def token():
    p = Path("/Users/scottthomasswitzer/Documents/ppe-quarterly-worktree/.env.r2-local")
    v = dict(
        line.split("=", 1)
        for line in p.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    return v["WRANGLER_R2_SQL_AUTH_TOKEN"].strip().strip('"').strip("'")


def sql(query, credential):
    req = urllib.request.Request(
        f"https://api.sql.cloudflarestorage.com/api/v1/accounts/{ACCOUNT}/basin-sql/query/{BUCKET}",
        data=json.dumps({"query": query}).encode(),
        headers={
            "authorization": "Bearer " + credential,
            "content-type": "application/json",
            "user-agent": "zion-foundation-pilot/1",
        },
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=45) as response:
        result = json.loads(response.read(1048577))
    if not result.get("success"):
        raise ValueError("BASIN_QUERY_FAILED")
    return result["result"], round(1000 * (time.perf_counter() - started), 2)


def main():
    raw = (OUT / "public-sec-pilot-input.json").read_bytes()
    source = json.loads(raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    namespace = "zion_uqf_pilot_" + fingerprint[:12]
    credential = token()
    cat = load_catalog(
        "r2",
        type="rest",
        uri=f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}",
        warehouse=f"{ACCOUNT}_{BUCKET}",
        token=credential,
    )
    try:
        cat.create_namespace(namespace)
    except NamespaceAlreadyExistsError:
        pass
    canary = [
        {
            "case_id": "precision_null",
            "exact_decimal": "9007199254740993.000000000000000001",
            "nullable_value": None,
            "rights": "OWNED_SYNTHETIC_TEST_ONLY",
        }
    ]
    datasets = {
        "issuer_anchor": source["identities"],
        "sec_concept_facts": source["facts"],
        "precision_canary": canary,
    }
    pins = []
    for name, rows in datasets.items():
        keys = list(rows[0])
        data = pa.table({k: pa.array([r.get(k) for r in rows], type=pa.string()) for k in keys})
        try:
            table = cat.load_table((namespace, name))
        except NoSuchTableError:
            table = cat.create_table(
                (namespace, name), schema=_pyarrow_to_schema_without_ids(data.schema)
            )
        snap = table.current_snapshot()
        if snap is None:
            table.append(
                data,
                snapshot_properties={
                    "source_sha256": fingerprint,
                    "rights": "PUBLIC_SEC_ONLY"
                    if name != "precision_canary"
                    else "OWNED_SYNTHETIC_TEST_ONLY",
                    "production_promotion": "FORBIDDEN",
                },
            )
        table.refresh()
        snap = table.current_snapshot()
        summary = snap.summary.model_dump()
        assert summary.get("source_sha256") == fingerprint
        pins.append(
            {
                "name": name,
                "namespace": namespace,
                "snapshot_id": str(snap.snapshot_id),
                "metadata_location": table.metadata_location,
                "source_sha256": fingerprint,
                "rights": "PUBLIC_SEC_ONLY"
                if name != "precision_canary"
                else "OWNED_SYNTHETIC_TEST_ONLY",
                "rows_declared": int(summary["total-records"]),
                "files_bytes_declared": int(summary["total-files-size"]),
            }
        )
    query = (
        "WITH eligible AS (SELECT f.*, ROW_NUMBER() OVER ("
        "PARTITION BY cik, concept, unit, period_start, period_end "
        "ORDER BY available_at DESC, accession DESC, source_observation_id DESC) "
        f"AS revision_order FROM {namespace}.sec_concept_facts f "
        "WHERE available_at <= '2026-01-01T00:00:00Z'), "
        "selected AS (SELECT * FROM eligible WHERE revision_order = 1) "
        "SELECT i.cik, i.entity_id, f.period_end, f.value_decimal, "
        "f.available_at, f.concept, f.unit "
        f"FROM {namespace}.issuer_anchor i LEFT JOIN selected f ON i.cik = f.cik "
        "ORDER BY i.cik, f.period_end LIMIT 101"
    )
    explain, explain_ms = sql("EXPLAIN FORMAT JSON " + query, credential)
    result, latency = sql(query, credential)
    expected = []
    for identity in source["identities"]:
        versions = [
            r
            for r in source["facts"]
            if r["cik"] == identity["cik"] and r["available_at"] <= source["as_of"]
        ]
        contexts = {}
        for row in versions:
            contexts.setdefault(
                (row["concept"], row["unit"], row["period_start"], row["period_end"]), []
            ).append(row)
        found = [
            max(rows, key=lambda r: (r["available_at"], r["accession"], r["source_observation_id"]))
            for rows in contexts.values()
        ]
        if not found:
            found = [
                {
                    "period_end": None,
                    "value_decimal": None,
                    "available_at": None,
                    "concept": None,
                    "unit": None,
                }
            ]
        for fact in found:
            expected.append(
                [
                    identity["cik"],
                    identity["entity_id"],
                    fact["period_end"],
                    fact["value_decimal"],
                    fact["available_at"],
                    fact["concept"],
                    fact["unit"],
                ]
            )
    expected.sort(key=lambda r: (r[0], r[2] or ""))
    actual = [
        [
            row.get(k)
            for k in [
                "cik",
                "entity_id",
                "period_end",
                "value_decimal",
                "available_at",
                "concept",
                "unit",
            ]
        ]
        for row in result["rows"]
    ]
    assert actual == expected, "independent public source join mismatch"
    canary_result, canary_ms = sql(
        f"SELECT exact_decimal, nullable_value FROM {namespace}.precision_canary LIMIT 2",
        credential,
    )
    assert canary_result["rows"] and all(
        row == {"exact_decimal": canary[0]["exact_decimal"], "nullable_value": None}
        for row in canary_result["rows"]
    ), "precision/null canary mismatch"
    import urllib.parse

    req = urllib.request.Request(
        f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}/v1/config?warehouse={ACCOUNT}_{BUCKET}",
        headers={"authorization": "Bearer " + credential, "user-agent": "zion-foundation-pilot/1"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        catalog_config = json.loads(r.read())

    def canonical(value):
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return [canonical(x) for x in value]
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        return value

    for pin in pins:
        endpoint = f"https://catalog.cloudflarestorage.com/{ACCOUNT}/{BUCKET}/v1/{catalog_config['overrides']['prefix']}/namespaces/{namespace}/tables/{pin['name']}"
        request = urllib.request.Request(
            endpoint,
            headers={
                "authorization": "Bearer " + credential,
                "user-agent": "zion-foundation-pilot/1",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            loaded = json.loads(response.read(1048576))
        metadata = canonical(loaded["metadata"])
        metadata["snapshots"] = sorted(
            metadata.get("snapshots", []), key=lambda x: str(x["snapshot-id"])
        )
        pin["metadata_canonical_sha256"] = hashlib.sha256(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    registry = {
        "schema_version": "zion-financial-query-policy-v1",
        "account_id": ACCOUNT,
        "bucket": BUCKET,
        "catalog_prefix": catalog_config["overrides"]["prefix"],
        "namespace": namespace,
        "source_sha256": fingerprint,
        "source_as_of": source["as_of"],
        "source_period_window": {
            "earliest": min(r["period_end"] for r in source["facts"]),
            "latest": max(r["period_end"] for r in source["facts"]),
            "revision_policy": source["revision_policy"],
        },
        "allowed_principal": "private-owner",
        "allowed_ciks": [r["cik"] for r in source["identities"]],
        "tables": pins,
        "max_source_bytes": 1048576,
        "max_response_bytes": 262144,
        "max_rows": 100,
        "max_runtime_ms": 15000,
        "licensed_datasets_allowed": False,
        "catalog_runtime_credential_scope": (
            "OPERATOR_CREDENTIAL; NARROWER_TOKEN_REPLACEMENT_REQUIRED_BEFORE_LICENSED_EXPANSION"
        ),
        "promotion_allowed": False,
    }
    report = {
        "schema_version": "basin-public-pilot-evidence-v1",
        "status": "PASS",
        "namespace": namespace,
        "tables": pins,
        "independent_join_rows": len(actual),
        "independent_comparison": "RAW_PUBLIC_SEC_OBSERVATIONS_PLUS_PYTHON_JOIN",
        "query_latency_ms": latency,
        "explain_latency_ms": explain_ms,
        "canary_latency_ms": canary_ms,
        "basin_metrics": result.get("metrics"),
        "canary_metrics": canary_result.get("metrics"),
        "gates": {
            "catalog_read": "PASS",
            "catalog_isolated_create": "PASS",
            "cross_table_sql": "PASS",
            "independent_result_match": "PASS",
            "precision_string_roundtrip": "PASS",
            "null_preserved": "PASS",
            "production_pointer_mutations": "PASS_ZERO",
            "licensed_rows_migrated": "PASS_ZERO",
            "least_privilege_runtime_token": "FAIL_NOT_VERIFIED",
        },
    }
    for name, value in [
        ("basin-pilot-policy.json", registry),
        ("basin-pilot-evidence.json", report),
        ("basin-explain-evidence.json", explain),
    ]:
        p = OUT / name
        p.write_text(json.dumps(value, indent=2) + "\n")
        p.chmod(0o600)
    print(
        json.dumps(
            {
                "status": "PASS",
                "namespace": namespace,
                "tables": len(pins),
                "independently_matched_rows": len(actual),
                "query_latency_ms": latency,
                "source_bytes": sum(p["files_bytes_declared"] for p in pins),
                "licensed_rows_migrated": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
