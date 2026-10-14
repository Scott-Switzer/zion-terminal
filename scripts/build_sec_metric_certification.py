"""Bounded public-SEC accounting companions; never mutate snapshots or cloud objects."""

import argparse
import hashlib
import json
import sys
import zlib
from collections import Counter
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/cloudflare-query/src"))
import duckdb
from financial_certification import LINEAR_IDENTITIES, VERSION, certify_history
from materialize_sec_engine import extract, materialize
from sec_engine import REGISTRY, encoded, history
from temporal_core import normalize_legacy_date_only

EXTRA = {
    "CostOfRevenue": "cost_of_revenue",
    "CostOfGoodsAndServicesSold": "cost_of_revenue",
    "GrossProfit": "gross_profit",
    "OperatingExpenses": "operating_expenses",
}
COHORT = [
    "0000320193",
    "0000789019",
    "0001045810",
    "0000019617",
    "0000070858",
    "0000886982",
    "0001403161",
    "0001141391",
    "0000200406",
    "0000078003",
    "0000310158",
    "0000021344",
    "0000077476",
    "0000080424",
    "0000104169",
    "0000354950",
    "0000060667",
    "0001744489",
    "0000050863",
    "0000858877",
]

BASE = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale")
OUT = BASE.parent / "unified-research-sprint/certification"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def original_record(record):
    return {
        key: format(value, "f") if isinstance(value, Decimal) else value
        for key, value in record.items()
    }


def raw_records(source, cik):
    """Independent raw source-ID/value inventory; no materialized values reused."""
    records = {}
    for tag, definition in source.get("facts", {}).get("us-gaap", {}).items():
        for unit, rows in definition.get("units", {}).items():
            for row in rows:
                original = original_record(row)
                sid = digest(
                    encoded({"cik": cik, "concept": tag, "unit": unit, "source_record": original})
                )
                if row.get("val") is not None:
                    try:
                        value = Decimal(str(row["val"]))
                        if value.is_finite():
                            records[sid] = {
                                "value": value,
                                "record": original,
                                "concept": tag,
                                "unit": unit,
                            }
                    except Exception:
                        continue
    return records


def extra_sources(source, cik, member):
    observations = []
    for tag, metric in EXTRA.items():
        for unit, rows in (
            source.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).items()
        ):
            if unit != "USD":
                continue
            for row in rows:
                try:
                    start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
                    if (
                        start > end
                        or end > date.fromisoformat(row["filed"])
                        or row["form"] not in ("10-K", "10-K/A", "10-Q", "10-Q/A")
                        or not row.get("accn")
                    ):
                        continue
                    number = Decimal(str(row["val"]))
                    if not number.is_finite():
                        continue
                    available = normalize_legacy_date_only(row["filed"]).iso_utc
                except (ValueError, KeyError, TypeError):
                    continue
                original = original_record(row)
                sid = digest(
                    encoded({"cik": cik, "concept": tag, "unit": unit, "source_record": original})
                )
                observations.append(
                    {
                        "cik": cik,
                        "metric": metric,
                        "concept": "us-gaap:" + tag,
                        "unit": unit,
                        "currency": "USD",
                        "period_start": start.isoformat(),
                        "period_end": end.isoformat(),
                        "available_at": available,
                        "accession": row["accn"],
                        "form": row["form"],
                        "value_decimal": format(number, "f"),
                        "source_observation_id": sid,
                        "member_sha256": member,
                        "source_record": original,
                        "availability_policy": "DATE_ONLY_NEXT_DAY_ET_V1",
                        "record_kind": "NORMALIZED_SOURCE_OBSERVATION",
                    }
                )
    return list({r["source_observation_id"]: r for r in observations}.values())


def duck_sum(connection, values, coefficients):
    decimals = [Decimal(value) for value in values]
    scale = max(0, max(-value.as_tuple().exponent for value in decimals))
    integer = max(max(1, value.adjusted() + 1) for value in decimals)
    if integer + scale + len(str(max(1, len(values)))) > 38:
        raise ValueError("INDEPENDENT_DECIMAL_PRECISION_NOT_SUPPORTED")
    sql = (
        f"SELECT sum(CAST(value AS DECIMAL(38,{scale})) * coefficient) "
        "FROM (SELECT unnest(?::VARCHAR[]) AS value, "
        "unnest(?::BIGINT[]) AS coefficient)"
    )
    return connection.execute(sql, [values, coefficients]).fetchone()[0]


def build_companion(raw, cik, release_id, registry_sha256, as_of, *, date_from="2014-01-01"):
    source = json.loads(raw, parse_float=Decimal)
    if str(source.get("cik")).zfill(10) != cik:
        raise ValueError("SOURCE_IDENTITY_MISMATCH")
    member = digest(raw)
    data = materialize(raw, cik, {}, {}, release_id, registry_sha256)
    base_ids = {r["source_observation_id"] for r in data["observations"]}
    additions = extra_sources(source, cik, member)
    data = {**data, "observations": data["observations"] + additions}
    result = history(
        data,
        metrics=list(REGISTRY),
        frequency="annual",
        date_from=date_from,
        date_to=as_of[:10],
        as_of=as_of,
    )
    # Auxiliary tags never become financial-history metrics; only identity inputs.
    raw_index = raw_records(source, cik)
    lookup = {r["source_observation_id"]: r for r in data["observations"]}
    connection = duckdb.connect(":memory:")
    connection.execute("SET threads=1")
    connection.execute("SET memory_limit='128MB'")
    connection.execute("SET enable_external_access=false")
    checks, identity_checks, accounting, gaps = [], [], [], []
    try:
        for row in result["observations"]:
            terms = row["source_terms"]
            values = [str(raw_index[t["source_observation_id"]]["value"]) for t in terms]
            coefficients = [t["coefficient"] for t in terms]
            expected = Fraction(Decimal(row["value_decimal"]))
            python_value = sum(
                (Fraction(Decimal(v)) * c for v, c in zip(values, coefficients)), Fraction()
            )
            sql_value = Fraction(duck_sum(connection, values, coefficients))
            status = "PASS" if expected == python_value == sql_value else "FAIL"
            checks.append(
                {
                    key: row.get(key)
                    for key in (
                        "metric",
                        "frequency",
                        "period_start",
                        "period_end",
                        "unit",
                        "value_decimal",
                    )
                }
                | {"source_ids": row["source_observation_ids"], "status": status}
            )
            if status != "PASS":
                raise ValueError("INDEPENDENT_RAW_VALUE_RECONCILIATION_FAILED")
            if (
                row["metric"] not in ("revenue", "operating_income")
                or len(row["source_observation_ids"]) != 1
            ):
                continue
            target = lookup[row["source_observation_ids"][0]]
            identity_id = next(
                name for name, (metric, _) in LINEAR_IDENTITIES.items() if metric == row["metric"]
            )
            roles = LINEAR_IDENTITIES[identity_id][1]
            chosen = {}
            for role in roles:
                candidates = [
                    part
                    for part in data["observations"]
                    if part["metric"] == role
                    and all(
                        part.get(key) == target.get(key)
                        for key in (
                            "period_start",
                            "period_end",
                            "accession",
                            "form",
                            "available_at",
                            "unit",
                            "currency",
                        )
                    )
                ]
                amounts = {part["value_decimal"] for part in candidates}
                if len(amounts) > 1:
                    gaps.append(
                        {
                            "metric": row["metric"],
                            "period_start": row["period_start"],
                            "period_end": row["period_end"],
                            "reason": "CONFLICTING_ACCOUNTING_COMPONENT_ALTERNATIVES",
                            "role": role,
                        }
                    )
                    break
                if not candidates:
                    gaps.append(
                        {
                            "metric": row["metric"],
                            "period_start": row["period_start"],
                            "period_end": row["period_end"],
                            "reason": "SAME_FILING_ACCOUNTING_COMPONENT_MISSING",
                            "role": role,
                        }
                    )
                    break
                chosen[role] = min(candidates, key=lambda part: part["source_observation_id"])
            if set(chosen) != set(roles):
                continue
            values = [
                str(raw_index[chosen[role]["source_observation_id"]]["value"]) for role in roles
            ]
            coefficients = list(roles.values())
            difference = sum(
                (Fraction(Decimal(v)) * c for v, c in zip(values, coefficients)), Fraction()
            )
            sql_difference = Fraction(duck_sum(connection, values, coefficients))
            evidence = {
                "identity_id": identity_id,
                "metric": row["metric"],
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "component_ids": {role: chosen[role]["source_observation_id"] for role in roles},
            }
            accounting.append(evidence)
            identity_checks.append(
                {
                    **evidence,
                    "python_status": "PASS" if difference == 0 else "FAIL",
                    "duckdb_status": "PASS" if sql_difference == 0 else "FAIL",
                    "difference_numerator": str(difference.numerator),
                    "difference_denominator": str(difference.denominator),
                }
            )
    finally:
        connection.close()
    proof = {
        "method": "INDEPENDENT_RAW_SOURCE_TERMS_AND_DUCKDB_DECIMAL",
        "as_of": normalize_legacy_date_only(as_of[:10]).iso_utc if len(as_of) == 10 else as_of,
        "implementation_sha256": digest(Path(__file__).read_bytes()),
        "member_sha256": member,
        "registry_sha256": registry_sha256,
        "checks": checks,
        "scope": "EXACT_RAW_SOURCE_VALUE_AND_LINEAR_TERMS;PERIOD_DEFINITION_GUARDS_SEPARATE",
    }
    certification = certify_history(
        data, result, as_of=as_of, reconciliation_evidence=proof, accounting_inputs=accounting
    )
    needed = {sid for item in accounting for sid in item["component_ids"].values()}
    return {
        "schema_version": "sec-metric-accounting-companion-v1",
        "certification_version": VERSION,
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "cik": cik,
        "label": source.get("entityName"),
        "build_context_release_id": release_id,
        "member_sha256": member,
        "registry_sha256": registry_sha256,
        "as_of": as_of,
        "observations": [
            row
            for row in additions
            if row["source_observation_id"] in needed
            and row["source_observation_id"] not in base_ids
        ],
        "accounting_inputs": accounting,
        "accounting_gaps": gaps,
        "identity_checks": identity_checks,
        "reconciliation_evidence": proof,
        "certification": certification,
        "qualification": (
            "APPEND_ONLY_ACCOUNTING_COMPANION;EXACT_MEMBER_BINDING_REQUIRED;"
            "NO_FINANCIAL_SNAPSHOT_MUTATION"
        ),
    }


def extend_historical(companion, raw, cutoffs, *, date_from="2014-01-01"):
    """Append independently executed, exact-cutoff proofs from an already pinned member."""
    if digest(raw) != companion["member_sha256"]:
        raise ValueError("HISTORICAL_MEMBER_HASH_MISMATCH")
    if not 1 <= len(set(cutoffs)) <= 32:
        raise ValueError("HISTORICAL_CUTOFF_BUDGET")
    result = dict(companion)
    proofs = dict(result.get("historical_reconciliation", {}))
    additions = {r["source_observation_id"]: r for r in result.get("observations", [])}
    identities = {encoded(r): r for r in result.get("accounting_inputs", [])}
    checks = {encoded(r): r for r in result.get("identity_checks", [])}
    for cutoff in sorted(set(cutoffs)):
        if not cutoff.endswith("T00:00:00Z") or len(cutoff) != 20:
            raise ValueError("EXPLICIT_UTC_SESSION_START_REQUIRED")
        date.fromisoformat(cutoff[:10])
        built = build_companion(
            raw,
            result["cik"],
            result.get(
                "source_materialization_release_id",
                result.get("build_context_release_id", "historical-local-proof"),
            ),
            result["registry_sha256"],
            cutoff,
            date_from=date_from,
        )
        proofs[cutoff] = built["reconciliation_evidence"]
        additions.update({r["source_observation_id"]: r for r in built["observations"]})
        identities.update({encoded(r): r for r in built["accounting_inputs"]})
        checks.update({encoded(r): r for r in built["identity_checks"]})
    result["historical_reconciliation"] = proofs
    result["observations"] = list(additions.values())
    result["accounting_inputs"] = list(identities.values())
    result["identity_checks"] = list(checks.values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciks", default=",".join(COHORT))
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--date-from", default="2014-01-01")
    args = parser.parse_args()
    ciks = args.ciks.split(",")
    if not 1 <= len(ciks) <= 20 or len(ciks) != len(set(ciks)):
        raise ValueError("BOUNDED_ONE_TO_TWENTY_COHORT_REQUIRED")
    manifest = json.loads((BASE / "engine-manifest.json").read_text())
    source = manifest["source"]
    if (
        manifest.get("rights") != "PUBLIC_SEC_ONLY"
        or not source["key"].startswith("sec/bulk/")
        or not source["key"].endswith("/companyfacts.zip")
    ):
        raise ValueError("PUBLIC_SEC_ARCHIVE_REQUIRED")
    entries = json.loads((BASE / "full-member-index.json").read_text())
    # Range client only instantiated if an exact local public-SEC member is absent.
    archive = None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    total_bytes = 0
    for cik in ciks:
        path = args.output_dir / (cik + ".companion.json")
        if path.exists():
            companion = json.loads(path.read_text())
            if companion["certification_version"] != VERSION or companion[
                "reconciliation_evidence"
            ]["implementation_sha256"] != digest(Path(__file__).read_bytes()):
                raise ValueError("EXISTING_COMPANION_IMPLEMENTATION_CHANGED")
        else:
            entry = entries[cik]
            if (
                entry["uncompressed_bytes"] > 24 * 1048576
                or entry["compressed_bytes"] > 8 * 1048576
            ):
                raise ValueError("MEMBER_BUDGET_OR_FORMAT")
            local = BASE / (cik + ".companyfacts.json")
            raw = (
                local.read_bytes()
                if local.exists() and local.stat().st_size <= 24 * 1048576
                else None
            )
            if (
                raw is None
                or len(raw) != entry["uncompressed_bytes"]
                or zlib.crc32(raw) != entry["crc"]
            ):
                if archive is None:
                    from reconcile_financial_inventory import client
                    from sec_universe_pipeline import CachedRangeArchive

                    archive = CachedRangeArchive(client(), source)
                raw = extract(archive, source, entry)
            companion = build_companion(
                raw,
                cik,
                manifest["release_id"],
                manifest["registry_sha256"],
                manifest["maximum_as_of"],
                date_from=args.date_from,
            )
            versions_path = OUT.parent / "certification-source-versions.json"
            if versions_path.exists():
                version = json.loads(versions_path.read_text())["companies"].get(cik)
                if version:
                    if version["member_sha256"] != companion["member_sha256"]:
                        raise ValueError("SOURCE_MATERIALIZATION_MEMBER_MISMATCH")
                    companion.update(
                        financial_serving_release_id=version["financial_serving_release_id"],
                        source_materialization_release_id=version[
                            "source_materialization_release_id"
                        ],
                        source_materialization_ref=version["payload_ref"],
                    )
            payload = encoded(companion)
            if len(payload) > 2 * 1048576 or total_bytes + len(payload) > 20 * 1048576:
                raise ValueError("COMPANION_OUTPUT_BUDGET")
            path.write_bytes(payload)
        total_bytes += path.stat().st_size
        summary = Counter(row["status"] for row in companion["certification"]["observations"])
        reports.append(
            {
                "cik": cik,
                "status_counts": dict(summary),
                "identity_checks": len(companion["identity_checks"]),
                "identity_failures": sum(
                    r["python_status"] == "FAIL" or r["duckdb_status"] == "FAIL"
                    for r in companion["identity_checks"]
                ),
                "bytes": path.stat().st_size,
                "sha256": digest(path.read_bytes()),
            }
        )
        print(
            json.dumps(
                {
                    "cik": cik,
                    "status_counts": dict(summary),
                    "identity_checks": len(companion["identity_checks"]),
                }
            ),
            flush=True,
        )
    summary = {
        "status": "BUILT_LOCAL_PUBLIC_SEC_COMPANIONS",
        "companies": reports,
        "companies_checked": len(reports),
        "accounting_certified_companies": sum(
            r["status_counts"].get("ACCOUNTING_CERTIFIED", 0) > 0 for r in reports
        ),
        "output_bytes": total_bytes,
        "archive_range_requests": archive.requests if archive else 0,
        "archive_bytes_read": archive.bytes_read if archive else 0,
        "cloud_writes": 0,
        "new_provider_acquisition": False,
        "source": source,
        "certification_version": VERSION,
    }
    (args.output_dir / "companion-summary.json").write_bytes(encoded(summary))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "status",
                    "companies_checked",
                    "accounting_certified_companies",
                    "output_bytes",
                    "archive_range_requests",
                    "archive_bytes_read",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
