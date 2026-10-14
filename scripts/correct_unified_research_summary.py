"""Immutable metadata-only aggregate correction. Reuses every financial/market source reference."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def correct_summary(old, companions):
    ciks = [r["cik"] for r in old["companies"]]
    if len(ciks) != len(set(ciks)) or set(ciks) != set(companions):
        raise ValueError("DISTINCT_CIK_COHORT_REQUIRED")
    statuses = Counter()
    observations = 0
    gaps = 0
    proofs = 0
    verified = []
    for cik, doc in sorted(companions.items()):
        if (
            doc.get("cik") != cik
            or doc.get("rights") != "PUBLIC_SEC_ONLY"
            or doc.get("licensed_sources_allowed") is not False
        ):
            raise ValueError("PUBLIC_SEC_COMPANION_REQUIRED")
        rows = doc["certification"]["observations"]
        checks = doc["reconciliation_evidence"]["checks"]
        if len(checks) != len(rows) or any(r.get("status") != "PASS" for r in checks):
            raise ValueError("INDEPENDENT_PROOF_COUNT_MISMATCH")
        counts = Counter(r["status"] for r in rows)
        local_gaps = len(doc["certification"]["coverage_gaps"])
        observations += len(rows)
        gaps += local_gaps
        proofs += len(checks)
        statuses.update(counts)
        verified.append(
            dict(
                cik=cik,
                as_of=doc["as_of"] if "as_of" in doc else None,
                observation_count=len(rows),
                coverage_gap_count=local_gaps,
                observation_status_counts=dict(counts),
                independent_checks=len(checks),
            )
        )
    included_gaps = sum(r.get("status_counts", {}).get("UNAVAILABLE", 0) for r in old["companies"])
    declared = old["union_current_series_observations"]
    cause = (
        "MIXED_OBSERVATION_AND_GAP_COUNTS"
        if declared == observations + included_gaps and included_gaps > 0
        else "DECLARED_AGGREGATE_DISCREPANCY"
    )
    corrected = dict(old)
    corrected.update(
        union_current_series_observations=observations,
        union_accounting_certified_observations=statuses["ACCOUNTING_CERTIFIED"],
        observation_count=observations,
        coverage_gap_count=gaps,
        observation_status_counts=dict(statuses),
        independent_reconciliation_checks=proofs,
        verified_company_counts=verified,
        aggregate_correction=dict(
            cause=cause,
            predecessor_declared_observation_count=declared,
            verified_observation_count=observations,
            included_unavailable_gap_statuses=included_gaps,
            unique_ciks=len(ciks),
            duplicate_ciks=0,
            company_reference_metadata_retained=True,
            definition="OBSERVATIONS_ONLY;COVERAGE_GAP_RECORDS_COUNTED_SEPARATELY",
        ),
    )
    return corrected


def successor_identity(predecessor_ref, summary_ref):
    return dict(
        schema_version="unified-summary-correction-v1",
        predecessor_root=predecessor_ref,
        corrected_summary=dict(
            relative_key="certification-summary.json",
            sha256=summary_ref["sha256"],
            bytes=summary_ref["bytes"],
        ),
    )


def successor_root(old, predecessor_ref, summary_ref, release_id):
    identity = successor_identity(predecessor_ref, summary_ref)
    if hashlib.sha256(encode(identity)).hexdigest() != release_id:
        raise ValueError("SUCCESSOR_CONTENT_IDENTITY_MISMATCH")
    root = dict(old)
    root.update(
        release_id=release_id,
        certification_summary=summary_ref,
        predecessor_root=predecessor_ref,
        content_identity=identity,
    )
    if "research_release_id" in root:
        root["research_release_id"] = release_id
    return root


def publish(previous_dir, output, s3):
    from publish_unified_research_release import PREFIX, Publisher, public_sec, read_verified, ref

    receipt = json.loads((previous_dir / "publication.json").read_bytes())
    if receipt.get("status") != "PASS":
        raise ValueError("PREDECESSOR_PUBLICATION_NOT_VERIFIED")
    previous_ref = receipt["root"]
    previous_raw = read_verified(s3, previous_ref, 2 * 1024**2)
    old = json.loads(previous_raw)
    if (
        old.get("schema_version") != "unified-research-release-v1"
        or old.get("source_policy", {}).get("licensed_wrds_allowed") is not False
    ):
        raise ValueError("PREDECESSOR_SOURCE_POLICY_REQUIRED")
    old_summary = json.loads(read_verified(s3, old["certification_summary"], 1024**2))
    companions = {}
    for cik, reference in old["certifications"].items():
        raw = read_verified(s3, reference, 2 * 1024**2)
        doc = json.loads(raw)
        public_sec(doc)
        if (
            doc["cik"] != cik
            or doc["member_sha256"] != reference["member_sha256"]
            or doc["registry_sha256"] != reference["registry_sha256"]
        ):
            raise ValueError("COMPANION_SOURCE_BINDING_MISMATCH")
        companions[cik] = doc
    summary = correct_summary(old_summary, companions)
    if summary["aggregate_correction"]["cause"] != "MIXED_OBSERVATION_AND_GAP_COUNTS":
        raise ValueError("AGGREGATE_CAUSE_NOT_DEMONSTRATED")
    summary_raw = encode(summary)
    temporary_ref = dict(sha256=hashlib.sha256(summary_raw).hexdigest(), bytes=len(summary_raw))
    release_id = hashlib.sha256(encode(successor_identity(previous_ref, temporary_ref))).hexdigest()
    prefix = PREFIX + release_id + "/"
    summary_ref = ref(prefix + "certification-summary.json", summary_raw)
    root = successor_root(old, previous_ref, summary_ref, release_id)
    root_raw = encode(root)
    if len(summary_raw) + len(root_raw) > 1024**2:
        raise ValueError("METADATA_CORRECTION_BUDGET")
    writer = Publisher(s3)
    writer.write(summary_ref["key"], summary_raw)
    root_ref = writer.write(prefix + "manifest.json", root_raw)
    report = dict(
        status="PASS",
        release_id=release_id,
        root=root_ref,
        predecessor_root=previous_ref,
        financial_release_id=root["financial_release_id"],
        certifications=len(companions),
        indexed_financial_entries=len(root["financial_entries"]),
        demonstrated_point_joins=len(root["demonstrated_point_joins"]),
        observation_count=summary["observation_count"],
        coverage_gap_count=summary["coverage_gap_count"],
        observation_status_counts=summary["observation_status_counts"],
        aggregate_correction=summary["aggregate_correction"],
        cloud_create_calls=writer.creates,
        cloud_bytes_written=writer.bytes,
        readback_hash_checks=writer.readbacks,
        existing_objects_verified=writer.existing_objects,
        source_references_reused=True,
        companion_objects_rewritten=0,
        price_objects_read=0,
        price_objects_rewritten=0,
        current_pointer_writes=0,
        configuration_writes=0,
        provider_requests=0,
        licensed_sources=0,
    )
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    for name, value in [
        ("publication.json", report),
        ("manifest.json", root),
        ("companion-summary.json", summary),
    ]:
        path = output / name
        path.write_bytes(encode(value))
        path.chmod(0o600)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--previous-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    from reconcile_financial_inventory import client

    try:
        print(json.dumps(publish(args.previous_dir, args.output, client())))
    except Exception as exc:
        print(json.dumps(dict(status="FAIL", error_type=type(exc).__name__)))
        raise SystemExit(1) from None
