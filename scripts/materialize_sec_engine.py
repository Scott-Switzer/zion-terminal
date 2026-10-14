"""Bounded resumable public SEC archive materialization; no provider or CURRENT writes."""

import argparse
import hashlib
import json
import re
import struct
import sys
import time
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "deploy/cloudflare-query/src"))
from reconcile_financial_inventory import client
from sec_engine import REGISTRY, VERSION, HistoryError, encoded, history, normalize_sources

OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale")
SAMPLE = (
    "320193 909832 1045810 1326801 1652044 19617 70858 72971 886982 895421 "
    "831001 36104 316709 5272 86312 899051 80661 1099219 1137774 1067983 "
    "4962 1364742 1390777 1156375 40545 18230 773840 315189 12927 66740 "
    "100885 1090727 1048911 101829 49826 936468 1133421 40533 1551182 32604 "
    "313616 104169 354950 60667 27419 29534 935703 56873 764478 109198 "
    "745732 320187 829224 63908 1041061 789019 1018724 1341439 51143 858877 "
    "50863 2488 804328 1730168 796343 1108524 1373715 896878 1065280 97476 "
    "723125 200406 78003 310158 1800 1551152 731766 64803 1739940 1613103 "
    "318154 882095 14272 59478 97745 34088 93410 1163165 87347 821189 "
    "797468 1534701 1035002 1510295 80424 21344 77476 1413329 764180 21665 "
    "55785 1103982 "
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def extract(s, source, entry):
    if (
        entry["flags"] & 1
        or entry["compression"] not in (0, 8)
        or entry["compressed_bytes"] > 8 * 1048576
        or entry["uncompressed_bytes"] > 24 * 1048576
    ):
        raise ValueError("MEMBER_BUDGET_OR_FORMAT")

    def read(offset, length):
        r = s.get_object(
            Bucket=source["bucket"],
            Key=source["key"],
            Range=f"bytes={offset}-{offset + length - 1}",
            IfMatch=source["etag"],
        )
        try:
            raw = r["Body"].read(length + 1)
        finally:
            r["Body"].close()
        if len(raw) != length:
            raise ValueError("RANGE_LENGTH")
        return raw

    h = struct.unpack("<4s5H3I2H", read(entry["offset"], 30))
    if h[0] != b"PK\x03\x04" or h[3] != entry["compression"] or h[2] != entry["flags"]:
        raise ValueError("ZIP_HEADER_MISMATCH")
    name = read(entry["offset"] + 30, h[-2]).decode("utf-8")
    if name != entry["name"]:
        raise ValueError("ZIP_MEMBER_IDENTITY")
    compressed = read(entry["offset"] + 30 + h[-2] + h[-1], entry["compressed_bytes"])
    if entry["compression"] == 8:
        decoder = zlib.decompressobj(-15)
        raw = decoder.decompress(compressed, 24 * 1048576 + 1)
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            raise ValueError("ZIP_STREAM")
    else:
        raw = compressed
    if len(raw) != entry["uncompressed_bytes"] or zlib.crc32(raw) != entry["crc"]:
        raise ValueError("ZIP_CRC_SIZE")
    return raw


def materialize(raw, cik, source, entry, release, registry_sha):
    c = json.loads(raw, parse_float=Decimal)
    sha = digest(raw)
    data = normalize_sources(c, cik, sha)
    data.update(
        release_id=release,
        registry_sha256=registry_sha,
        source_lineage={
            "rights": "PUBLIC_SEC_ONLY",
            "archive": source,
            "entry": entry,
            "member_sha256": sha,
            "cik": cik,
        },
    )
    tags = c.get("facts", {})
    periods = set()
    concepts = set()
    counts = Counter()
    first = None
    last = None
    for namespace, definitions in tags.items():
        for tag, definition in definitions.items():
            concepts.add(namespace + ":" + tag)
            for unit, rows in definition.get("units", {}).items():
                for row in rows:
                    counts["source_records"] += 1
                    if row.get("end"):
                        first = min(first or row["end"], row["end"])
                        last = max(last or row["end"], row["end"])
                        periods.add((row.get("start"), row["end"]))
                    if row.get("form") in ("10-K", "10-K/A", "10-Q", "10-Q/A") and "val" in row:
                        counts["candidate_us_periodic_records"] += 1
    data["source_inventory"] = {
        "counts": dict(counts),
        "unique_period_contexts": len(periods),
        "concepts": sorted(concepts),
        "period_end_range": [first, last],
    }
    return data


def evaluate_member(raw, cik, source, entry, release, registry_sha):
    try:
        return materialize(raw, cik, source, entry, release, registry_sha), None
    except HistoryError as ex:
        return None, ex.code
    except (ValueError, KeyError, TypeError):
        return None, "SOURCE_SCHEMA_OR_CONTEXT_INVALID"


def release_identity(source, registry_sha, entries, selected):
    return digest(
        encoded(
            {
                "source": source,
                "registry": registry_sha,
                "directory_sha256": digest(encoded(entries)),
                "materialized_ciks": sorted(selected),
            }
        )
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=100)
    a = p.parse_args()
    if not 1 <= a.count <= 20396:
        raise ValueError("BOUNDED_SAMPLE")
    directory = json.loads((OUT / "archive-directory.json").read_text())
    entries = {
        re.fullmatch(r"CIK(\d{10})\.json", x["name"])[1]: x
        for x in directory["entries"]
        if re.fullmatch(r"CIK(\d{10})\.json", x["name"])
    }
    old = json.loads(
        Path(
            "/Users/scottthomasswitzer/Documents/Secret/artifacts/"
            "sec-financial-history/source-lineage.json"
        ).read_text()
    )[0]["archive"]
    source = dict(old)
    assert source["etag"] == directory["etag"].strip('"')
    registry = {
        "version": VERSION,
        "metrics": REGISTRY,
        "implementation_sha256": digest(
            (Path(__file__).parents[1] / "deploy/cloudflare-query/src/sec_engine.py").read_bytes()
        ),
        "policy": ("DATA_CARRIED_STATEMENT_FAMILY;CONSERVATIVE_DEBT_QUARANTINE;"
                   "NO_INDUSTRY_INFERENCE"),
        "dependency_sha256": {
            name: digest(
                (Path(__file__).parents[1] / "deploy/cloudflare-query/src" / name).read_bytes()
            )
            for name in ("temporal_core.py", "new_york_tz.py", "sec_screener.py")
        },
        "publication_schema_version": "sec-engine-catalog-with-screen-v1",
    }
    rsha = digest(encoded(registry))
    (OUT / "registry.json").write_bytes(encoded(registry))
    (OUT / "full-member-index.json").write_bytes(encoded(entries))
    selected = list(
        dict.fromkeys(
            str(int(x)).zfill(10) for x in SAMPLE.split() if str(int(x)).zfill(10) in entries
        )
    )[: a.count]
    if len(selected) < a.count:
        selected += [x for x in sorted(entries) if x not in selected][: a.count - len(selected)]
    release = release_identity(source, rsha, entries, selected)
    manifest = {
        "schema_version": VERSION,
        "release_id": release,
        "rights": "PUBLIC_SEC_ONLY",
        "licensed_sources_allowed": False,
        "source": source,
        "registry_sha256": rsha,
        "date_from": "1900-01-01",
        "date_to": source["snapshot_date"],
        "maximum_as_of": source["snapshot_date"] + "T00:00:00Z",
        "companies": {},
        "quarantined": {},
        "archive_issuers": len(entries),
    }
    started = time.perf_counter()
    coverage = []
    s = client()
    h = s.head_object(Bucket=source["bucket"], Key=source["key"])
    assert h["ETag"].strip('"') == source["etag"] and h["ContentLength"] == source["bytes"]

    def prepare(cik):
        path = OUT / (cik + ".companyfacts.json")
        if not path.exists():
            raw = extract(s, source, entries[cik])
            path.write_bytes(raw)
            path.chmod(0o600)
        return cik, path

    # Four bounded I/O tasks; only the main thread parses/calculates financial histories.
    pool = ThreadPoolExecutor(max_workers=4)
    for i, (cik, path) in enumerate(pool.map(prepare, selected)):
        raw = path.read_bytes()
        assert (
            len(raw) == entries[cik]["uncompressed_bytes"]
            and zlib.crc32(raw) == entries[cik]["crc"]
        )
        data, error = evaluate_member(raw, cik, source, entries[cik], release, rsha)
        if error:
            manifest["quarantined"][cik] = {
                "reason": error,
                "member_sha256": digest(raw),
                "bytes": len(raw),
            }
            print(
                json.dumps(
                    {"company": i + 1, "cik": cik, "status": "QUARANTINED", "reason": error}
                ),
                flush=True,
            )
            continue
        body = encoded(data)
        if len(body) > 16 * 1048576 or len(data["observations"]) > 12000:
            raise ValueError("MATERIALIZED_BUDGET")
        (OUT / (cik + ".history.json")).write_bytes(body)
        counts = {}
        for freq in ("annual", "quarterly", "ttm"):
            # All existing source periods; no truncation. API paginates instead.
            result = history(
                data,
                metrics=list(REGISTRY),
                frequency=freq,
                date_from="1930-01-01",
                date_to=manifest["date_to"],
                as_of=manifest["maximum_as_of"],
            )
            result.update(source_release_id=release, registry_sha256=rsha)
            (OUT / (cik + "." + freq + ".expected.json")).write_bytes(encoded(result))
            counts[freq] = {
                m: {
                    "observations": sum(x["metric"] == m for x in result["observations"]),
                    "gaps": sum(x["metric"] == m for x in result["coverage_gaps"]),
                }
                for m in REGISTRY
            }
        if not any(v["observations"] for freq in counts.values() for v in freq.values()):
            manifest["quarantined"][cik] = {
                "reason": "NO_VALID_MAPPED_FINANCIAL_HISTORY",
                "member_sha256": digest(raw),
                "bytes": len(raw),
            }
            print(
                json.dumps(
                    {
                        "company": i + 1,
                        "cik": cik,
                        "status": "QUARANTINED",
                        "reason": "NO_VALID_MAPPED_FINANCIAL_HISTORY",
                    }
                ),
                flush=True,
            )
            continue
        ref = {
            "sha256": digest(body),
            "bytes": len(body),
            "label": data["label"],
            "member_sha256": data["member_sha256"],
            "policy": data["policy"],
            "normalized_revisions": len(data["observations"]),
            "coverage": counts,
            "source_inventory": {
                k: v for k, v in data["source_inventory"].items() if k != "concepts"
            },
        }
        manifest["companies"][cik] = ref
        coverage.append({"cik": cik, **ref})
        print(
            json.dumps(
                {
                    "company": i + 1,
                    "cik": cik,
                    "label": data["label"],
                    "revisions": len(data["observations"]),
                    "family": data["policy"]["family"],
                }
            ),
            flush=True,
        )
    pool.shutdown(wait=True)
    (OUT / "engine-manifest.json").write_bytes(encoded(manifest))
    (OUT / "company-coverage.json").write_bytes(encoded(coverage))
    receipt = {
        "release_id": release,
        "issuers_processed": len(selected),
        "companies": len(manifest["companies"]),
        "quarantined_issuers": len(manifest["quarantined"]),
        "quarantine_reasons": dict(Counter(x["reason"] for x in manifest["quarantined"].values())),
        "archive_issuers": len(entries),
        "source_records_verified": sum(
            x["source_inventory"]["counts"]["source_records"] for x in coverage
        ),
        "normalized_revisions": sum(x["normalized_revisions"] for x in coverage),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "largest_payload_bytes": max(x["bytes"] for x in coverage),
        "source_original_archives_unchanged": True,
    }
    (OUT / "materialization.json").write_bytes(encoded(receipt))
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
