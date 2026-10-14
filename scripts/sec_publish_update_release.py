"""Source-refresh-only SEC catalog composer; financial revision promotion is not supported.

Keeps the archive effective cutoff, independently verifies the cached raw snapshot,
and rejects any changed financial observations. No CURRENT/config/provider writes.
"""

import argparse
import contextlib
import io
import json
import tempfile
from fractions import Fraction
from pathlib import Path

from materialize_sec_engine import REGISTRY, digest, encoded, history
from sec_incremental_update import bounded_read
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
    release = digest(
        encoded(
            dict(
                parent_manifest_sha256=parent_ref["sha256"],
                candidate_id=candidate["release_id"],
                registry_sha256=root["registry_sha256"],
            )
        )
    )
    prefix = "gold/sec-engine/releases/" + release + "/"
    budget = Budget()
    new = dict(root, release_id=release)
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
        # Preserve parent effective cutoff; all inherited indexes remain reproducible.
        results = {
            frequency: history(
                data,
                metrics=list(REGISTRY),
                frequency=frequency,
                date_from="1930-01-01",
                date_to=root["date_to"],
                as_of=root["maximum_as_of"],
            )
            for frequency in ("annual", "quarterly", "ttm")
        }
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
        if report["financial_observation_changes"]:
            raise ValueError("SOURCE_REFRESH_ONLY_GATE_REQUIRES_NO_FINANCIAL_CHANGES")
        body = encoded(data)
        payloads[cik] = body
        item["materialized"] = dict(
            key=prefix + "companies/" + cik + ".json",
            sha256=digest(body),
            bytes=len(body),
            member_sha256=data["member_sha256"],
            source_release_id=release,
        )
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
        replacements[cik] = company_row(cik, data, results["annual"]["observations"])
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
        financial_observation_changes=False,
        limitation="GLOBAL_FINANCIAL_CUTOFF_UNCHANGED;PROVIDER_RESPONSE_REFRESH_ONLY;NO_MIXED_CUTOFF_ADVANCE",
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
        financial_observation_changes=False,
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
