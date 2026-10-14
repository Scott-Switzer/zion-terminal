"""Controlled per-company SEC updates; fixed historical screening cutoff.

Prior-cutoff histories must reproduce exactly; new-cutoff histories are independently
verified. No CURRENT/config/provider writes.
"""

import argparse
import contextlib
import io
import json
import tempfile
from fractions import Fraction
from pathlib import Path

from materialize_sec_engine import REGISTRY, digest, encoded, history
from sec_incremental_update import bounded_read, instant
from sec_release_promotion import candidate_overlay, validate_candidate_inputs
from sec_screener import company_row, exact

BUCKET = "financial-system-datasets"


class Budget:
    def __init__(self, maximum_bytes=200_000_000, maximum_creates=500):
        self.maximum_bytes = maximum_bytes
        self.maximum_creates = maximum_creates
        self.bytes = 0
        self.creates = 0

    def reserve(self, size):
        if (
            size < 0
            or self.bytes + size > self.maximum_bytes
            or self.creates + 1 > self.maximum_creates
        ):
            raise ValueError("PUBLICATION_BUDGET")
        self.bytes += size
        self.creates += 1


def read(s, ref, limit):
    if not 0 <= ref["bytes"] <= limit:
        raise ValueError("READ_BUDGET")
    response = s.get_object(Bucket=BUCKET, Key=ref["key"])
    try:
        raw = response["Body"].read(ref["bytes"] + 1)
    finally:
        response["Body"].close()
    if len(raw) != ref["bytes"] or digest(raw) != ref["sha256"]:
        raise ValueError("ARTIFACT_HASH_MISMATCH")
    return raw


def pin(s, prefix, name, raw, budget):
    budget.reserve(len(raw))
    key = prefix + name
    try:
        s.put_object(
            Bucket=BUCKET, Key=key, Body=raw, ContentType="application/json", IfNoneMatch="*"
        )
    except Exception as error:
        if getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
    ref = dict(key=key, sha256=digest(raw), bytes=len(raw))
    if read(s, ref, len(raw)) != raw:
        raise ValueError("IMMUTABLE_OBJECT_CONFLICT")
    return ref


def ordered_rows(rows, metric):
    def ordering(row):
        value = row["metrics"].get(metric)
        number = (
            (
                Fraction(int(value["numerator"]), int(value["denominator"]))
                if metric == "revenue_growth"
                else exact(value["value_decimal"])
            )
            if value is not None
            else Fraction(0)
        )
        return value is None, number, row["cik"]

    return sorted(rows, key=ordering)


def chunks(rows, maximum=2 * 1048576):
    current = []
    size = 2
    for row in rows:
        length = len(encoded(row)) + 1
        if length > maximum:
            raise ValueError("SCREEN_ROW_BUDGET")
        if current and size + length > maximum - 512:
            yield current
            current = []
            size = 2
        current.append(row)
        size += length
    if current:
        yield current


def independent_check(data, raw, results, registry_sha):
    import verify_sec_engine_independent as independent

    with tempfile.TemporaryDirectory(prefix="sec-update-verify-") as directory:
        target = Path(directory)
        cik = data["cik"]
        body = encoded(data)
        (target / (cik + ".companyfacts.json")).write_bytes(raw)
        (target / (cik + ".history.json")).write_bytes(body)
        for frequency, result in results.items():
            (target / (cik + "." + frequency + ".expected.json")).write_bytes(encoded(result))
        (target / "engine-manifest.json").write_bytes(
            encoded(
                dict(
                    release_id=data["release_id"],
                    registry_sha256=registry_sha,
                    companies={cik: dict(member_sha256=data["member_sha256"], sha256=digest(body))},
                )
            )
        )
        previous = independent.OUT
        try:
            independent.OUT = target
            with contextlib.redirect_stdout(io.StringIO()):
                independent.main()
            receipt = json.loads((target / "independent-reconciliation.json").read_text())
        finally:
            independent.OUT = previous
    if receipt["status"] != "PASS":
        raise ValueError("INDEPENDENT_RECONCILIATION_FAILED")
    return {k: v for k, v in receipt.items() if k != "checks"}


def history_signature(result, source_data=None):
    """Financial parity with independently resolvable component lineage.

    Snapshot metadata may change raw IDs. Resolve every signed term back to its
    financial context; do not equate different components merely by net value.
    """
    sources = {
        row["source_observation_id"]: row for row in (source_data or {}).get("observations", [])
    }
    citation_fields = {
        "member_sha256",
        "source_observation_id",
        "source_observation_ids",
        "source_record_ids",
    }

    def financial_context(row):
        context = {
            key: row.get(key)
            for key in (
                "concept",
                "unit",
                "currency",
                "value_decimal",
                "period_start",
                "period_end",
                "available_at",
                "accession",
                "form",
            )
        }
        context["filed"] = row.get("source_record", {}).get("filed")
        return context

    def clean(value):
        if isinstance(value, dict):
            output = {
                k: clean(v)
                for k, v in value.items()
                if k not in citation_fields and k != "source_terms"
            }
            if "source_terms" in value:
                terms = {}
                for term in value["source_terms"]:
                    sid = term["source_observation_id"]
                    if sid not in sources:
                        raise ValueError("FINANCIAL_COMPONENT_SOURCE_MISSING")
                    context = financial_context(sources[sid])
                    key = encoded(context)
                    terms.setdefault(key, dict(context=context, coefficient=0))["coefficient"] += (
                        term["coefficient"]
                    )
                output["source_terms"] = sorted(
                    (v for v in terms.values() if v["coefficient"]), key=encoded
                )
            return output
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return {
        key: sorted((clean(row) for row in result[key]), key=encoded)
        for key in ("observations", "coverage_gaps")
    }


def update_histories(prior, data, root, cutoff):
    if instant(cutoff) < instant(root["maximum_as_of"]):
        raise ValueError("UPDATE_CUTOFF_BEFORE_PARENT")
    old_results = {}
    current = {}
    screen = {}
    for frequency in ("annual", "quarterly", "ttm"):
        kwargs = dict(metrics=list(REGISTRY), frequency=frequency, date_from="1930-01-01")
        old = history(prior, **kwargs, date_to=root["date_to"], as_of=root["maximum_as_of"])
        fixed = history(data, **kwargs, date_to=root["date_to"], as_of=root["maximum_as_of"])
        if history_signature(old, prior) != history_signature(fixed, data):
            raise ValueError("HISTORICAL_AVAILABILITY_REPRODUCTION_FAILED")
        fresh = history(data, **kwargs, date_to=cutoff[:10], as_of=cutoff)
        old_metrics = {r["metric"] for r in old["observations"]}
        new_metrics = {r["metric"] for r in fresh["observations"]}
        if not old_metrics <= new_metrics:
            raise ValueError("SUPPORTED_METRIC_COVERAGE_LOST")
        old_results[frequency] = old
        current[frequency] = fresh
        screen[frequency] = fixed
    return (
        current,
        screen,
        dict(
            status="PASS",
            frequencies=["annual", "quarterly", "ttm"],
            comparison="FINANCIAL_FIELDS_COMPONENT_CONTEXT_AND_COEFFICIENTS_GAP_SEMANTICS;RAW_ID_CHANGES_RETAINED_IN_RECONCILIATION",
            supported_metric_coverage="NO_LOSS",
            historical_cutoff=root["maximum_as_of"],
        ),
    )


def publish_update(s, parent_ref, original_ref, base_dir, candidate_dir, output_dir):
    _, candidate, _, _ = validate_candidate_inputs(base_dir, candidate_dir)
    root = json.loads(read(s, parent_ref, 1048576))
    original = json.loads(read(s, original_ref, 1048576))
    registry_raw = read(s, root["registry"], 4 * 1048576)
    if digest(registry_raw) != root["registry_sha256"] or registry_raw != bounded_read(
        Path(base_dir) / "registry.json"
    ):
        raise ValueError("CURRENT_REGISTRY_REQUIRED")
    for manifest in (root, original):
        if (
            manifest.get("rights") != "PUBLIC_SEC_ONLY"
            or manifest.get("licensed_sources_allowed") is not False
        ):
            raise ValueError("PUBLIC_SEC_ONLY_REQUIRED")
    helper_path = Path(__file__).parents[1] / "deploy/cloudflare-query/src/sec_balance_quality.py"
    helper_raw = bounded_read(helper_path)
    release = digest(
        encoded(
            dict(
                parent_manifest_sha256=parent_ref["sha256"],
                candidate_id=candidate["release_id"],
                balance_quality_version="sec-balance-same-vintage-v1",
                balance_quality_implementation_sha256=digest(helper_raw),
                registry_sha256=root["registry_sha256"],
            )
        )
    )
    prefix = "gold/sec-engine/releases/" + release + "/"
    budget = Budget()
    new = dict(root, release_id=release)
    new["balance_quality_version"] = "sec-balance-same-vintage-v1"
    # Explicit source-release allowlist preserves inherited immutable payload identities.
    new["source_releases"] = sorted(set(root.get("source_releases", [])) | {release})
    new["previous_releases"] = {
        **root.get("previous_releases", {}),
        root["release_id"]: parent_ref,
        original["release_id"]: original_ref,
    }
    new["shards"] = dict(root["shards"])
    changed_shards = {}
    replacements = {}
    reports = {}
    payloads = {}
    for cik in sorted(candidate["companies"]):
        suffix = cik[-2:]
        if suffix not in changed_shards:
            changed_shards[suffix] = json.loads(read(s, root["shards"][suffix], 8 * 1048576))
        item = changed_shards[suffix][cik]
        prior = json.loads(read(s, item["materialized"], 24 * 1048576))
        if prior["release_id"] not in root.get("source_releases", [root["release_id"]]):
            raise ValueError("UNAPPROVED_SOURCE_RELEASE")
        data, report = candidate_overlay(
            candidate_dir, cik, prior, release, root["registry_sha256"]
        )
        company_cutoff = candidate["companies"][cik]["maximum_as_of"]
        results, screen_results, historical_gate = update_histories(
            prior, data, root, company_cutoff
        )
        report["historical_reproduction"] = historical_gate
        if not any(result["observations"] for result in results.values()):
            raise ValueError("PROMOTION_GATE_NO_VALID_HISTORY")
        for result in results.values():
            result.update(source_release_id=release, registry_sha256=root["registry_sha256"])
        raw = bounded_read(Path(candidate_dir) / "raw" / cik / (data["member_sha256"] + ".json"))
        report["independent"] = independent_check(data, raw, results, root["registry_sha256"])
        report["financial_observation_changes"] = any(
            report["reconciliation"][k]
            for k in ("removed_source_ids", "added_source_ids", "revised_contexts")
        )
        if report["financial_observation_changes"] and not any(
            check["status"] == "PASS" for check in report["accounting"]
        ):
            raise ValueError("ACCOUNTING_PASS_REQUIRED_FOR_FINANCIAL_UPDATE")
        report["effective_maximum_as_of"] = company_cutoff
        post_cutoff = [
            row
            for row in data["observations"]
            if instant(row["available_at"]) > instant(root["maximum_as_of"])
        ]
        report["post_parent_financial_sources"] = dict(
            observations=len(post_cutoff),
            accessions=sorted({row["accession"] for row in post_cutoff}),
            availability_instants=sorted({row["available_at"] for row in post_cutoff}),
        )
        report["history_output_counts"] = {
            frequency: dict(
                parent_cutoff=len(screen_results[frequency]["observations"]),
                company_cutoff=len(results[frequency]["observations"]),
            )
            for frequency in results
        }
        report["coverage_anomaly"] = dict(
            normalized_observations=len(data["observations"]),
            normalized_payload_bytes=len(encoded(data)),
            supported_metrics_no_loss=True,
            source_rejection_counts=data.get("rejections", {}),
            rejected_context_count=len(data.get("rejected_contexts", [])),
            availability_excluded=candidate["companies"][cik].get("availability_excluded", 0),
            accounting_passes=sum(check["status"] == "PASS" for check in report["accounting"]),
            accounting_failures=sum(check["status"] == "FAIL" for check in report["accounting"]),
        )
        body = encoded(data)
        payloads[cik] = body
        item["materialized"] = dict(
            key=prefix + "companies/" + cik + ".json",
            sha256=digest(body),
            bytes=len(body),
            member_sha256=data["member_sha256"],
            source_release_id=release,
        )
        item["maximum_as_of"] = company_cutoff
        item["date_to"] = company_cutoff[:10]
        item["policy"] = data["policy"]
        item["coverage"] = {
            frequency: {
                metric: dict(
                    observations=sum(r["metric"] == metric for r in result["observations"]),
                    gaps=sum(r["metric"] == metric for r in result["coverage_gaps"]),
                )
                for metric in REGISTRY
            }
            for frequency, result in results.items()
        }
        replacements[cik] = company_row(cik, data, screen_results["annual"]["observations"])
        reports[cik] = report
    # Conservative preflight, then enforce exact cumulative cloud-create budget.
    screen_refs = root["screening_metrics"]["metrics"]
    if (
        sum(ref["bytes"] for refs in screen_refs.values() for ref in refs)
        + sum(map(len, payloads.values()))
        + sum(len(encoded(v)) for v in changed_shards.values())
        + 2 * 1048576
        > budget.maximum_bytes
    ):
        raise ValueError("PUBLICATION_BUDGET_PREFLIGHT")
    for cik, body in payloads.items():
        pin(s, prefix, "companies/" + cik + ".json", body, budget)
    for suffix, shard in changed_shards.items():
        new["shards"][suffix] = pin(
            s, prefix, "catalog/" + suffix + ".json", encoded(shard), budget
        )
    new_screen = {}
    for metric, refs in screen_refs.items():
        if sum(ref["bytes"] for ref in refs) > 32 * 1048576:
            raise ValueError("METRIC_INDEX_MEMORY_BUDGET")
        rows = []
        for ref in refs:
            value = json.loads(read(s, ref, 4 * 1048576))
            if (
                value["release_id"] != root["release_id"]
                or value["registry_sha256"] != root["registry_sha256"]
            ):
                raise ValueError("SCREEN_CONTEXT_MISMATCH")
            rows.extend(row for row in value["companies"] if row["cik"] not in replacements)
        for cik, row in replacements.items():
            rows.append(
                {
                    **{k: v for k, v in row.items() if k not in ("metrics", "missing")},
                    "metrics": {metric: row["metrics"][metric]} if metric in row["metrics"] else {},
                    "missing": {metric: row["missing"].get(metric)},
                }
            )
        if len({r["cik"] for r in rows}) != len(rows):
            raise ValueError("DUPLICATE_SCREEN_COMPANY")
        new_screen[metric] = [
            pin(
                s,
                prefix,
                "screen/" + metric + "/" + str(i) + ".json",
                encoded(
                    dict(
                        companies=part, release_id=release, registry_sha256=root["registry_sha256"]
                    )
                ),
                budget,
            )
            for i, part in enumerate(chunks(ordered_rows(rows, metric)))
        ]
    new["screening_metrics"] = dict(root["screening_metrics"], metrics=new_screen)
    new["update_refresh"] = dict(
        candidate_release_id=candidate["release_id"],
        retrieved_at=candidate["retrieved_at"],
        effective_maximum_as_of=root["maximum_as_of"],
        financial_observation_changes=any(
            r["financial_observation_changes"] for r in reports.values()
        ),
        per_company_cutoffs={cik: r["effective_maximum_as_of"] for cik, r in reports.items()},
        limitation="SCREEN_GLOBAL_CUTOFF_UNCHANGED;ONLY_HASH_PINNED_UPDATED_COMPANIES_HAVE_NEWER_HISTORY_CEILINGS",
    )
    new["balance_quality_implementation"] = pin(
        s, prefix, "implementation/sec_balance_quality.py", helper_raw, budget
    )
    rootref = pin(s, prefix, "manifest.json", encoded(new), budget)
    receipt = dict(
        status="PASS",
        release_id=release,
        root=rootref,
        parent_release_id=root["release_id"],
        parent_root=parent_ref,
        rollback_root=parent_ref,
        original_root=original_ref,
        candidate_release_id=candidate["release_id"],
        effective_maximum_as_of=root["maximum_as_of"],
        candidate_retrieved_at=candidate["retrieved_at"],
        financial_observation_changes=any(
            r["financial_observation_changes"] for r in reports.values()
        ),
        checks=reports,
        cloud_bytes_written=budget.bytes,
        cloud_create_calls=budget.creates,
        current_pointer_writes=0,
        configuration_writes=0,
        licensed_sources=0,
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("serving-manifest.json", new), ("publication.json", receipt)):
        path = output / name
        body = encoded(value)
        if path.exists() and path.read_bytes() != body:
            raise ValueError("LOCAL_IMMUTABLE_CONFLICT")
        path.write_bytes(body)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "parent-publication",
        "original-publication",
        "base-dir",
        "candidate-dir",
        "output-dir",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    from reconcile_financial_inventory import client

    result = publish_update(
        client(),
        json.loads(args.parent_publication.read_text())["root"],
        json.loads(args.original_publication.read_text())["root"],
        args.base_dir,
        args.candidate_dir,
        args.output_dir,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}))


if __name__ == "__main__":
    main()
