"""Reconcile private screen pages with the independently stored compact public index."""

import json
import sys
from pathlib import Path

from sec_engine_acceptance import OUT, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/cloudflare-query/src"))
from sec_screener import select  # noqa: E402


def main():
    index = json.loads((OUT / "screen-index.json").read_text())
    evidence = []
    for metric in ("revenue_growth", "operating_cash_flow", "free_cash_flow"):
        expected, gaps = select(index, metric, "0", None, "desc", None)
        params = dict(
            metric=metric, minimum="0", limit="25", expected_release_id=index["release_id"]
        )
        actual, seen = [], set()
        while True:
            result, measurement = request("/private/v1/financial-screen", params)
            assert measurement["http_status"] == 200, (metric, result)
            actual.extend(result["matches"])
            assert result["missing_companies"] == gaps
            cursor = result["next_cursor"]
            if cursor is None:
                break
            assert cursor not in seen
            seen.add(cursor)
            params["cursor"] = cursor
        assert actual == expected
        evidence.append(
            dict(metric=metric, status="PASS", matches=len(actual), missing_companies=len(gaps))
        )
    for params, auth, expected in (
        ({"metric": "revenue"}, False, 401),
        ({"metric": "revenue", "sql": "SELECT"}, True, 400),
        ({"as_of": "2020-01-01T00:00:00Z"}, True, 422),
        ({"minimum": "1e999999999"}, True, 422),
    ):
        _, measurement = request("/private/v1/financial-screen", params, auth=auth)
        assert measurement["http_status"] == expected
        evidence.append(dict(check="DENIAL", http_status=expected, status="PASS"))
    report = dict(
        status="PASS",
        release_id=index["release_id"],
        checks=evidence,
        qualification="LIVE_MATCH_TO_PINNED_INDEX;CORE_ARITHMETIC_TESTED_SEPARATELY",
    )
    (OUT / "screen-acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
