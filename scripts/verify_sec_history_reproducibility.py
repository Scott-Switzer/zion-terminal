# ruff: noqa: E501
"""Verify immutable bytes and stable histories across two public source releases."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "deploy/cloudflare-query/src"))
from extract_sec_history_sources import OUT
from reconcile_financial_inventory import client
from sec_history import REGISTRY, history


def main():
    old_key = "gold/sec-financial-history/releases/f9007e254cb6ba51f8993e8ad420e3d80f884a278611e8c30bd3969a7f6c5e61/manifest.json"
    old_sha = "1ae2aa35ee33e9434ce44535a68e0327c2d073c4f7f755c17e14b871ec0fea70"
    current = json.loads((OUT / "history-publication.json").read_text())
    s = client()

    def read(ref, maximum=16 * 1048576):
        raw = s.get_object(Bucket="financial-system-datasets", Key=ref["key"])["Body"].read(
            maximum + 1
        )
        assert len(raw) <= maximum and hashlib.sha256(raw).hexdigest() == ref["sha256"]
        return json.loads(raw)

    old = read({"key": old_key, "sha256": old_sha})
    new = read(current)
    assert (
        old["rights"] == new["rights"] == "PUBLIC_SEC_ONLY"
        and old["release_id"] != new["release_id"]
    )
    metrics = [m for m in REGISTRY if m != "total_debt"]
    checks = []
    for cik in new["companies"]:
        before = read(old["companies"][cik])
        after = read(new["companies"][cik])
        assert before["member_sha256"] == after["member_sha256"]
        for frequency in ["annual", "quarterly", "ttm"]:
            request = dict(
                metrics=metrics,
                frequency=frequency,
                date_from=new["date_from"],
                date_to=new["date_to"],
                as_of=new["maximum_as_of"],
            )
            a = history(before, **request)
            b = history(after, **request)
            assert (
                a["observations"] == b["observations"] and a["coverage_gaps"] == b["coverage_gaps"]
            )
            checks.append(
                {
                    "cik": cik,
                    "frequency": frequency,
                    "status": "PASS",
                    "observations": len(b["observations"]),
                }
            )
    result = {
        "status": "PASS",
        "old_release": old["release_id"],
        "new_release": new["release_id"],
        "checks": checks,
        "old_immutable_manifest_sha256_unchanged": old_sha,
        "scope": "CURRENT_QUERY_ALGORITHM_OVER_TWO_PUBLIC_IMMUTABLE_SOURCE_RELEASES;EXCLUDES_INTENTIONALLY_CHANGED_DEBT_MAPPING",
        "production_pointer_changes": 0,
    }
    (OUT / "immutable-reproducibility.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "checks": len(checks)}))


if __name__ == "__main__":
    main()
