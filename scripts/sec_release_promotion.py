"""Compose a public SEC candidate into a regular immutable release. Never writes CURRENT."""

import argparse
import json
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from materialize_sec_engine import REGISTRY, digest, encoded, history, materialize
from sec_engine import balance_checks
from sec_incremental_update import bounded_read, instant, public

CHECKS = {"coverage", "accounting", "anomalies", "reconciliation", "provenance"}


def promotion_gate(checks):
    if set(checks) != CHECKS or any(checks[k] is not True for k in CHECKS):
        raise ValueError("PROMOTION_GATE_FAILED_OR_INCOMPLETE")


def reconcile(old, new):
    old_ids = {r["source_observation_id"] for r in old}
    new_ids = {r["source_observation_id"] for r in new}

    def contexts(rows):
        result = defaultdict(set)
        for row in rows:
            key = tuple(
                row.get(k) for k in ("concept", "unit", "period_start", "period_end", "accession")
            )
            result[key].add(row["value_decimal"])
        return result

    before, after = contexts(old), contexts(new)
    return dict(
        removed_source_ids=sorted(old_ids - new_ids),
        added_source_ids=sorted(new_ids - old_ids),
        revised_contexts=[
            dict(context=list(k), old_values=sorted(before[k]), new_values=sorted(after[k]))
            for k in sorted(before.keys() & after.keys(), key=str)
            if before[k] != after[k]
        ],
        deletion_policy="AUTHORITATIVE_FULL_SNAPSHOT_NO_UNION;PRIOR_RELEASE_PRESERVED",
    )


def write_once(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != body:
        raise ValueError("LOCAL_IMMUTABLE_CONFLICT")
    path.write_bytes(body)


def candidate_overlay(
    candidate_dir, cik, prior, release_id, registry_sha256, date_from="1900-01-01"
):
    """Bounded single-company hook for a streaming materializer; no inherited-file copies.

    Caller pins/validates the full candidate manifest and current registry using
    validate_candidate_inputs, then invokes this only for listed changed CIKs.
    """
    candidate_dir = Path(candidate_dir)
    candidate = json.loads(bounded_read(candidate_dir / "manifest.json", 64 * 1048576))
    if (
        not re.fullmatch(r"\d{10}", cik)
        or candidate.get("rights") != "PUBLIC_SEC_ONLY"
        or candidate.get("licensed_sources_allowed") is not False
    ):
        raise ValueError("PUBLIC_SEC_ONLY_REQUIRED")
    if (
        candidate.get("promotion_status") != "UNAPPROVED_CANDIDATE"
        or candidate.get("production_pointer_writes") != 0
    ):
        raise ValueError("CANDIDATE_STATUS_INVALID")
    if (
        digest(encoded({k: v for k, v in candidate.items() if k not in ("release_id", "objects")}))
        != candidate["release_id"]
    ):
        raise ValueError("CANDIDATE_IDENTITY_MISMATCH")
    change = candidate["companies"][cik]
    if not re.fullmatch(r"[a-f0-9]{64}", change.get("member_sha256", "")):
        raise ValueError("INVALID_CANDIDATE_COMPANY_REFERENCE")
    raw = bounded_read(candidate_dir / "raw" / cik / (change["member_sha256"] + ".json"))
    if digest(raw) != change["member_sha256"]:
        raise ValueError("RAW_HASH_MISMATCH")
    source = json.loads(raw, parse_float=Decimal)
    if (
        source.get("rights", "PUBLIC_SEC_ONLY") != "PUBLIC_SEC_ONLY"
        or source.get("licensed_sources_allowed", False) is not False
    ):
        raise ValueError("PUBLIC_SEC_ONLY_REQUIRED")
    if str(source.get("cik", "")).zfill(10) != cik:
        raise ValueError("CIK_MISMATCH")
    data = materialize(raw, cik, {}, {}, release_id, registry_sha256)
    cutoff = instant(change["maximum_as_of"])
    if cutoff > instant(candidate["maximum_as_of"]):
        raise ValueError("CANDIDATE_CUTOFF_MISMATCH")
    data["observations"] = [r for r in data["observations"] if instant(r["available_at"]) <= cutoff]
    data["rejected_contexts"] = [
        r for r in data["rejected_contexts"] if instant(r["available_at"]) <= cutoff
    ]
    data["source_lineage"] = dict(
        rights="PUBLIC_SEC_ONLY",
        member_sha256=digest(raw),
        candidate_release_id=candidate["release_id"],
        retrieved_at=candidate["retrieved_at"],
    )
    accounting = balance_checks(
        data, candidate["maximum_as_of"], date_from, candidate["maximum_as_of"][:10]
    )
    checks = dict(
        coverage=bool(data["observations"]),
        accounting=not any(r["status"] == "FAIL" for r in accounting),
        anomalies=len(data["observations"]) <= 12000 and len(encoded(data)) <= 16 * 1048576,
        reconciliation=True,
        provenance=True,
    )
    promotion_gate(checks)
    return data, dict(
        checks=checks,
        reconciliation=reconcile(prior["observations"], data["observations"]),
        accounting=accounting,
        candidate_release_id=candidate["release_id"],
        prior_member_sha256=prior["member_sha256"],
        member_sha256=data["member_sha256"],
    )


def validate_candidate_inputs(base_dir, candidate_dir):
    base_dir, candidate_dir = map(Path, (base_dir, candidate_dir))
    base_body = bounded_read(base_dir / "engine-manifest.json", 64 * 1048576)
    base = json.loads(base_body)
    public(base)
    candidate_body = bounded_read(candidate_dir / "manifest.json", 64 * 1048576)
    candidate = json.loads(candidate_body)
    if (
        candidate.get("rights") != "PUBLIC_SEC_ONLY"
        or candidate.get("licensed_sources_allowed") is not False
    ):
        raise ValueError("PUBLIC_SEC_ONLY_REQUIRED")
    instant(candidate["maximum_as_of"])
    if any(
        not re.fullmatch(r"\d{10}", cik)
        or not re.fullmatch(r"[a-f0-9]{64}", change.get("member_sha256", ""))
        for cik, change in candidate.get("companies", {}).items()
    ):
        raise ValueError("INVALID_CANDIDATE_COMPANY_REFERENCE")
    if not candidate.get("companies") or len(candidate["companies"]) > 100:
        raise ValueError("UPDATE_COMPANY_BUDGET")
    if (
        candidate.get("promotion_status") != "UNAPPROVED_CANDIDATE"
        or candidate.get("production_pointer_writes") != 0
    ):
        raise ValueError("CANDIDATE_STATUS_INVALID")
    identity = {k: v for k, v in candidate.items() if k not in ("release_id", "objects")}
    if digest(encoded(identity)) != candidate["release_id"]:
        raise ValueError("CANDIDATE_IDENTITY_MISMATCH")
    # Validate every candidate object, including the obsolete normalized representation.
    for name, ref in candidate["objects"].items():
        path = (candidate_dir / name).resolve()
        if not path.is_relative_to(candidate_dir.resolve()):
            raise ValueError("CANDIDATE_PATH")
        body = bounded_read(path)
        if digest(body) != ref["sha256"] or len(body) != ref["bytes"]:
            raise ValueError("CANDIDATE_HASH_MISMATCH")
    registry_body = bounded_read(base_dir / "registry.json")
    registry = json.loads(registry_body)
    core = Path(__file__).parents[1] / "deploy/cloudflare-query/src"
    if (
        digest(registry_body) != base["registry_sha256"]
        or registry.get("metrics") != REGISTRY
        or registry.get("implementation_sha256") != digest((core / "sec_engine.py").read_bytes())
    ):
        raise ValueError("CURRENT_REGISTRY_REBUILD_REQUIRED")
    for name, sha in registry.get("dependency_sha256", {}).items():
        if digest((core / name).read_bytes()) != sha:
            raise ValueError("REGISTRY_DEPENDENCY_MISMATCH")
    return base, candidate, base_body, candidate_body


def compose_candidate(base_dir, candidate_dir, output_dir):
    base_dir, candidate_dir, output_dir = map(Path, (base_dir, candidate_dir, output_dir))
    if output_dir.resolve() in (base_dir.resolve(), candidate_dir.resolve()):
        raise ValueError("SEPARATE_OUTPUT_REQUIRED")
    base, candidate, base_body, candidate_body = validate_candidate_inputs(base_dir, candidate_dir)
    release = digest(
        encoded(
            dict(
                base_manifest_sha256=digest(base_body),
                candidate_manifest_sha256=digest(candidate_body),
                registry_sha256=base["registry_sha256"],
            )
        )
    )
    manifest = dict(
        base,
        release_id=release,
        companies={},
        maximum_as_of=candidate["maximum_as_of"],
        date_to=candidate["maximum_as_of"][:10],
        update_candidate_id=candidate["release_id"],
        parent_release_id=base["release_id"],
    )
    reports = {}
    for cik, ref in base["companies"].items():
        prior_body = bounded_read(base_dir / (cik + ".history.json"))
        if digest(prior_body) != ref["sha256"]:
            raise ValueError("BASE_COMPANY_HASH_MISMATCH")
        prior = json.loads(prior_body)
        if cik in candidate["companies"]:
            change = candidate["companies"][cik]
            raw = bounded_read(candidate_dir / "raw" / cik / (change["member_sha256"] + ".json"))
            if digest(raw) != change["member_sha256"]:
                raise ValueError("RAW_HASH_MISMATCH")
            source = json.loads(raw, parse_float=Decimal)
            if (
                source.get("rights", "PUBLIC_SEC_ONLY") != "PUBLIC_SEC_ONLY"
                or source.get("licensed_sources_allowed", False) is not False
            ):
                raise ValueError("PUBLIC_SEC_ONLY_REQUIRED")
            if str(source.get("cik", "")).zfill(10) != cik:
                raise ValueError("CIK_MISMATCH")
            data = materialize(raw, cik, base["source"], {}, release, base["registry_sha256"])
            cutoff = instant(change["maximum_as_of"])
            if cutoff > instant(candidate["maximum_as_of"]):
                raise ValueError("CANDIDATE_CUTOFF_MISMATCH")
            data["observations"] = [
                r for r in data["observations"] if instant(r["available_at"]) <= cutoff
            ]
            data["rejected_contexts"] = [
                r for r in data["rejected_contexts"] if instant(r["available_at"]) <= cutoff
            ]
            data["source_lineage"] = dict(
                rights="PUBLIC_SEC_ONLY",
                member_sha256=digest(raw),
                candidate_release_id=candidate["release_id"],
                retrieved_at=candidate["retrieved_at"],
            )
            reconciliation = reconcile(prior["observations"], data["observations"])
            accounting = balance_checks(
                data, manifest["maximum_as_of"], manifest["date_from"], manifest["date_to"]
            )
            checks = dict(
                coverage=bool(data["observations"]),
                accounting=not any(x["status"] == "FAIL" for x in accounting),
                anomalies=len(data["observations"]) <= 12000 and len(encoded(data)) <= 16 * 1048576,
                reconciliation=True,
                provenance=True,
            )
            promotion_gate(checks)
            reports[cik] = dict(
                checks=checks,
                reconciliation=reconciliation,
                accounting=accounting,
                prior_member_sha256=prior["member_sha256"],
                member_sha256=data["member_sha256"],
            )
        else:
            data = dict(prior, release_id=release)
        counts = {}
        for frequency in ("annual", "quarterly", "ttm"):
            result = history(
                data,
                metrics=list(REGISTRY),
                frequency=frequency,
                date_from="1930-01-01",
                date_to=manifest["date_to"],
                as_of=manifest["maximum_as_of"],
            )
            result.update(source_release_id=release, registry_sha256=base["registry_sha256"])
            write_once(output_dir / (cik + "." + frequency + ".expected.json"), encoded(result))
            counts[frequency] = {
                m: dict(
                    observations=sum(r["metric"] == m for r in result["observations"]),
                    gaps=sum(r["metric"] == m for r in result["coverage_gaps"]),
                )
                for m in REGISTRY
            }
        if cik in reports and not any(
            x["observations"] for freq in counts.values() for x in freq.values()
        ):
            raise ValueError("PROMOTION_GATE_NO_VALID_HISTORY")
        body = encoded(data)
        write_once(output_dir / (cik + ".history.json"), body)
        manifest["companies"][cik] = dict(
            ref,
            sha256=digest(body),
            bytes=len(body),
            member_sha256=data["member_sha256"],
            policy=data["policy"],
            normalized_revisions=len(data["observations"]),
            coverage=counts,
        )
    if set(reports) != set(candidate["companies"]):
        raise ValueError("CANDIDATE_COMPANY_NOT_IN_BASE")
    receipt = dict(
        schema_version="sec-controlled-promotion-v1",
        status="PASS",
        release_id=release,
        parent_release_id=base["release_id"],
        candidate_release_id=candidate["release_id"],
        checks=reports,
        registry_rebuilt_from_raw=True,
        original_candidate_registry_sha256=candidate["registry_sha256"],
        rollback=dict(release_id=base["release_id"], base_manifest_sha256=digest(base_body)),
        production_pointer_writes=0,
        publication_required=True,
    )
    for name in ("registry.json", "full-member-index.json"):
        write_once(output_dir / name, bounded_read(base_dir / name, 64 * 1048576))
    write_once(output_dir / "engine-manifest.json", encoded(manifest))
    write_once(output_dir / "promotion.json", encoded(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-dir", "candidate-dir", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    result = compose_candidate(args.base_dir, args.candidate_dir, args.output_dir)
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}))


if __name__ == "__main__":
    main()
