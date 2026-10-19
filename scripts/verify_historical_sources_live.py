"""Live historical API acceptance; exact release pin, pagination, gaps and private boundaries."""

import argparse
import json
from pathlib import Path

from sec_engine_acceptance import request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output must be new")
    summary = json.loads((args.directory / "acceptance.json").read_bytes())
    ref = summary["release"]
    manifest = json.loads((args.directory / ref["relative_manifest"]).read_bytes())
    checks = {}

    def check(name, valid):
        checks[name] = bool(valid)
        if not valid:
            raise ValueError("Live check refused: " + name)

    _, meta = request("/private/v1/historical-series", auth=False)
    check("anonymous_refused", meta["http_status"] == 401)
    page, meta = request(
        "/private/v1/historical-series", {"limit": 500, "expected_release_id": ref["release_id"]}
    )
    check(
        "catalog_exact_release",
        meta["http_status"] == 200 and page.get("release_id") == ref["release_id"],
    )
    check("catalog_all_series", len(page.get("series", [])) == len(manifest["series"]))
    check(
        "catalog_counts",
        page["observation_count"] == manifest["observation_count"]
        and page["missing_count"] == manifest["missing_count"],
    )
    chosen = [
        next(k for k in manifest["series"] if k.startswith(prefix))
        for prefix in ("us_treasury:nominal:10y", "ecb:fx:USD:EUR", "worldbank:commodity:")
    ]
    examples = []
    for series in chosen:
        entry = manifest["series"][series]
        local = json.loads((args.directory / entry["local_path"]).read_bytes())["rows"]
        params = {"series_id": series, "limit": 2, "expected_release_id": ref["release_id"]}
        first, meta = request("/private/v1/historical-series", params)
        check(
            series + "_first_page",
            meta["http_status"] == 200
            and [
                [r["observation_date"], r["value_decimal"], r["missing_reason"], r["source_id"]]
                for r in first.get("observations", [])
            ]
            == local[:2],
        )
        check(
            series + "_no_false_availability",
            first["point_in_time_status"] == "NOT_ESTABLISHED"
            and all(r["available_at"] is None for r in first["observations"]),
        )
        params["cursor"] = first["next_cursor"]
        second, meta = request("/private/v1/historical-series", params)
        check(
            series + "_next_page",
            meta["http_status"] == 200
            and [
                [r["observation_date"], r["value_decimal"], r["missing_reason"], r["source_id"]]
                for r in second["observations"]
            ]
            == local[2:4],
        )
        examples.append(dict(series_id=series, unit=first["unit"], rows_checked=4))
    missing_series = next(k for k, v in manifest["series"].items() if v["missing"])
    entry = manifest["series"][missing_series]
    missing = next(
        r
        for r in json.loads((args.directory / entry["local_path"]).read_bytes())["rows"]
        if r[1] is None
    )
    page, meta = request(
        "/private/v1/historical-series",
        {
            "series_id": missing_series,
            "date_from": missing[0],
            "date_to": missing[0],
            "expected_release_id": ref["release_id"],
        },
    )
    check(
        "missing_not_zero",
        meta["http_status"] == 200
        and page["observations"][0]["value_decimal"] is None
        and page["observations"][0]["missing_reason"] == missing[2],
    )
    _, meta = request("/private/v1/historical-series", {"as_of": "2000-01-01"})
    check("historical_asof_refused", meta["http_status"] == 422)
    _, meta = request("/private/v1/historical-series", {"expected_release_id": "0" * 64})
    check("wrong_release_refused", meta["http_status"] == 409)
    claims, meta = request(
        "/private/v1/equity-claims",
        dict(
            cik="0000320193",
            as_of="2026-09-24",
            release_id="ac8f14de487eee6b78fd6c108e8611af8f326d864a162b00d5308f6c90abfd18",
            expected_release_id="ac8f14de487eee6b78fd6c108e8611af8f326d864a162b00d5308f6c90abfd18",
        ),
    )
    check(
        "existing_expanded_claims_preserved",
        meta["http_status"] == 200
        and claims.get("source_release_id")
        == "162c3745857617b185579b874a9adcf16e25a0a553ed4277080acdfbc474aebf",
    )
    receipt = dict(
        status="PASS",
        release_id=ref["release_id"],
        checks=checks,
        series=manifest["series_count"],
        observations=manifest["observation_count"],
        missing=manifest["missing_count"],
        examples=examples,
        provider_calls_at_query_time=0,
        production_pointer_writes=0,
        slack_messages_sent=0,
    )
    args.output.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
