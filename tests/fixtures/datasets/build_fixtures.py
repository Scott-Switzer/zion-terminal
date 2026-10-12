"""Generate the synthetic private dataset fixture tree used by registry tests.

The generated tree mirrors the real store layout closely enough to exercise
pointer resolution, manifest hash pinning, bounded discovery, source fallback and
the fundamentals contract, while containing no real issuer or licensed data.

Regenerate with::

    python tests/fixtures/datasets/build_fixtures.py
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TREE = ROOT / "tree"
CORPUS = "sec-filing-intelligence/corpus/v1/" + "a" * 64
SERVING_RELEASE = "1" * 32
SERVING_MANIFEST = "gold/serving/releases/{}/manifest.json".format(SERVING_RELEASE)
SEC_FACTS_RELEASE = "b" * 64
SEC_FACTS_PREFIX = f"gold/sec-facts/releases/{SEC_FACTS_RELEASE}"
PACKET_RELEASE = "c" * 64
BULK_SOURCE = "sec-nightly-20260101"

TSTA_CIK = "0000000001"
TSTB_CIK = "0000000002"


def write(store: str, path: str, payload: bytes | str) -> None:
    target = TREE / store / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload.encode() if isinstance(payload, str) else payload)


def write_json(store: str, path: str, value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    write(store, path, payload)
    return hashlib.sha256(payload.encode()).hexdigest()


def usd(*rows: dict) -> dict:
    return {"units": {"USD": list(rows)}}


def shares(*rows: dict) -> dict:
    return {"units": {"shares": list(rows)}}


def fact(
    *,
    start: str | None,
    end: str,
    value: int,
    accession: str,
    filed: str,
    form: str = "10-K",
    frame: str | None = None,
) -> dict:
    row = {
        "end": end,
        "val": value,
        "accn": accession,
        "fy": int(end[:4]),
        "fp": "FY",
        "form": form,
        "filed": filed,
    }
    if start:
        row["start"] = start
    if frame:
        row["frame"] = frame
    return row


def annual_rows(
    start_end_values: list[tuple[str, str, int]], accession: str, filed: str
) -> list[dict]:
    return [
        fact(start=start, end=end, value=value, accession=accession, filed=filed)
        for start, end, value in start_end_values
    ]


def companyfacts_a() -> dict:
    """Issuer A: rich facts with deliberate contract edge cases."""
    a10k = "0000000001-26-000010"
    a10k_prev = "0000000001-26-000001"
    # Three eligible annual periods plus a fourth reporting the same revenue with a
    # different value, which must be superseded by the later filing.
    revenue_rows = annual_rows(
        [
            ("2022-01-01", "2022-12-31", 900),
            ("2023-01-01", "2023-12-31", 1000),
            ("2024-01-01", "2024-12-31", 1100),
            ("2025-01-01", "2025-12-31", 1200),
        ],
        a10k,
        "2026-02-10",
    )
    revenue_rows.append(
        fact(
            start="2024-01-01",
            end="2024-12-31",
            value=1150,
            accession=a10k_prev,
            filed="2025-02-12",
        )
    )
    # Second revenue concept disagrees in the latest period: a real concept conflict.
    return {
        "cik": int(TSTA_CIK),
        "entityName": "FIXTURE ISSUER A",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": usd(*revenue_rows),
                "Revenues": usd(
                    fact(
                        start="2025-01-01",
                        end="2025-12-31",
                        value=1188,
                        accession="0000000001-26-000011",
                        filed="2026-03-01",
                    )
                ),
                "OperatingIncomeLoss": usd(
                    *annual_rows(
                        [
                            ("2023-01-01", "2023-12-31", 200),
                            ("2024-01-01", "2024-12-31", 250),
                            ("2025-01-01", "2025-12-31", 300),
                        ],
                        a10k,
                        "2026-02-10",
                    )
                ),
                "NetIncomeLoss": usd(
                    *annual_rows(
                        [
                            ("2023-01-01", "2023-12-31", 150),
                            ("2024-01-01", "2024-12-31", 180),
                            ("2025-01-01", "2025-12-31", 210),
                        ],
                        a10k,
                        "2026-02-10",
                    )
                ),
                "NetIncomeLossAvailableToCommonStockholdersBasic": usd(
                    *annual_rows(
                        [
                            ("2023-01-01", "2023-12-31", 140),
                            ("2024-01-01", "2024-12-31", 170),
                            ("2025-01-01", "2025-12-31", 200),
                        ],
                        a10k,
                        "2026-02-10",
                    )
                ),
                # Depreciation and amortization is filed after the report as-of instant.
                "DepreciationDepletionAndAmortization": usd(
                    *annual_rows(
                        [
                            ("2023-01-01", "2023-12-31", 60),
                            ("2024-01-01", "2024-12-31", 66),
                        ],
                        a10k,
                        "2026-02-10",
                    ),
                    fact(
                        start="2025-01-01",
                        end="2025-12-31",
                        value=72,
                        accession="0000000001-27-000002",
                        filed="2026-10-20",
                    ),
                ),
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "EUR": annual_rows(
                            [("2024-01-01", "2024-12-31", 40), ("2025-01-01", "2025-12-31", 44)],
                            a10k,
                            "2026-02-10",
                        )
                    }
                },
                "CashAndCashEquivalentsAtCarryingValue": usd(
                    fact(
                        start=None, end="2023-12-31", value=500, accession=a10k, filed="2026-02-10"
                    ),
                    fact(
                        start=None, end="2024-12-31", value=560, accession=a10k, filed="2026-02-10"
                    ),
                    fact(
                        start=None, end="2025-12-31", value=610, accession=a10k, filed="2026-02-10"
                    ),
                ),
                "LongTermDebtCurrent": usd(
                    fact(
                        start=None, end="2024-12-31", value=90, accession=a10k, filed="2026-02-10"
                    ),
                    fact(
                        start=None, end="2025-12-31", value=95, accession=a10k, filed="2026-02-10"
                    ),
                ),
                "LongTermDebtNoncurrent": usd(
                    fact(
                        start=None, end="2024-12-31", value=410, accession=a10k, filed="2026-02-10"
                    ),
                    fact(
                        start=None, end="2025-12-31", value=430, accession=a10k, filed="2026-02-10"
                    ),
                ),
                "LongTermDebt": usd(
                    fact(
                        start=None, end="2024-12-31", value=500, accession=a10k, filed="2026-02-10"
                    ),
                    fact(
                        start=None, end="2025-12-31", value=530, accession=a10k, filed="2026-02-10"
                    ),
                ),
                "CommercialPaper": usd(
                    fact(start=None, end="2025-12-31", value=20, accession=a10k, filed="2026-02-10")
                ),
                "WeightedAverageNumberOfDilutedSharesOutstanding": shares(
                    fact(
                        start="2024-01-01",
                        end="2024-12-31",
                        value=1000,
                        accession=a10k,
                        filed="2026-02-10",
                    ),
                    # A nine-month stub that cannot be an annual period.
                    fact(
                        start="2025-04-01",
                        end="2025-12-31",
                        value=1010,
                        accession=a10k,
                        filed="2026-02-10",
                    ),
                ),
                # Disclosure-only issuer tag: must never satisfy the D&A contract field.
                "Depreciation": usd(
                    *annual_rows(
                        [
                            ("2025-01-01", "2025-12-31", 51),
                        ],
                        a10k,
                        "2026-02-10",
                    )
                ),
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": shares(
                    fact(
                        start=None, end="2025-12-31", value=980, accession=a10k, filed="2026-02-10"
                    )
                )
            },
        },
    }


def companyfacts_b() -> dict:
    """Issuer B: facts reachable only through the private bulk archive member."""
    accession = "0000000002-26-000005"
    annual = [
        ("2023-01-01", "2023-12-31", 300),
        ("2024-01-01", "2024-12-31", 330),
        ("2025-01-01", "2025-12-31", 360),
    ]
    return {
        "cik": int(TSTB_CIK),
        "entityName": "FIXTURE ISSUER B",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": usd(
                    *annual_rows(annual, accession, "2026-02-20")
                ),
                "OperatingIncomeLoss": usd(
                    *annual_rows([(s, e, v // 3) for s, e, v in annual], accession, "2026-02-20")
                ),
                "NetIncomeLoss": usd(
                    *annual_rows([(s, e, v // 4) for s, e, v in annual], accession, "2026-02-20")
                ),
                "DepreciationDepletionAndAmortization": usd(
                    *annual_rows([(s, e, v // 20) for s, e, v in annual], accession, "2026-02-20")
                ),
                "PaymentsToAcquirePropertyPlantAndEquipment": usd(
                    *annual_rows([(s, e, v // 25) for s, e, v in annual], accession, "2026-02-20")
                ),
                "CashAndCashEquivalentsAtCarryingValue": usd(
                    fact(
                        start=None,
                        end="2025-12-31",
                        value=75,
                        accession=accession,
                        filed="2026-02-20",
                    )
                ),
                "LongTermDebtCurrent": usd(
                    fact(
                        start=None,
                        end="2025-12-31",
                        value=20,
                        accession=accession,
                        filed="2026-02-20",
                    )
                ),
                "LongTermDebtNoncurrent": usd(
                    fact(
                        start=None,
                        end="2025-12-31",
                        value=180,
                        accession=accession,
                        filed="2026-02-20",
                    )
                ),
                "LongTermDebt": usd(
                    fact(
                        start=None,
                        end="2025-12-31",
                        value=200,
                        accession=accession,
                        filed="2026-02-20",
                    )
                ),
                "WeightedAverageNumberOfDilutedSharesOutstanding": shares(
                    fact(
                        start="2025-01-01",
                        end="2025-12-31",
                        value=500,
                        accession=accession,
                        filed="2026-02-20",
                    )
                ),
            }
        },
    }


def serving_release() -> str:
    """Serving pointer, manifest and metric catalog; entity artifacts are absent."""
    catalog = [
        {
            "metric_id": "revenue",
            "observations": 10,
            "units": ["USD"],
            "issuer_count": 2,
            "period_types": ["annual"],
        },
        {
            "metric_id": "operating_income",
            "observations": 10,
            "units": ["USD"],
            "issuer_count": 2,
            "period_types": ["annual"],
        },
        {
            "metric_id": "net_income",
            "observations": 10,
            "units": ["USD"],
            "issuer_count": 2,
            "period_types": ["annual"],
        },
        {
            "metric_id": "cash",
            "observations": 10,
            "units": ["USD"],
            "issuer_count": 2,
            "period_types": ["annual", "quarterly"],
        },
        {
            "metric_id": "total_debt",
            "observations": 4,
            "units": ["USD"],
            "issuer_count": 2,
            "period_types": ["annual"],
        },
        {
            "metric_id": "shares_outstanding",
            "observations": 4,
            "units": ["shares"],
            "issuer_count": 2,
            "period_types": ["annual"],
        },
    ]
    catalog_sha = hashlib.sha256(
        json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": "financial-serving-v2",
        "serving_release_id": SERVING_RELEASE,
        "temporal_schema_version": "financial-temporal-v1",
        "entity_count": 2,
        "fundamentals_rows": 20,
        "prices_rows": 5,
        "corporate_action_count": 0,
        "artifacts": [
            {"path": "catalog/metrics.json", "sha256": catalog_sha, "bytes": 10},
            {"path": "identity/resolver_index.json", "sha256": "d" * 64, "bytes": 10},
            {
                "path": "entities/entity_sec_cik_0000000001/fundamentals/annual.json",
                "sha256": "e" * 64,
                "bytes": 10,
            },
        ],
    }
    manifest_key = write_json("financial", SERVING_MANIFEST, manifest)
    write_json(
        "financial", f"gold/serving/releases/{SERVING_RELEASE}/catalog/metrics.json", catalog
    )
    pointer = {
        "schema_version": "financial-serving-v2",
        "serving_release_id": SERVING_RELEASE,
        "manifest_key": SERVING_MANIFEST,
        "manifest_sha256": manifest_key,
    }
    pointer_bytes = json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode()
    write("financial", "gold/serving/coverage25/CURRENT.json", pointer_bytes)
    write("financial", "gold/serving/CURRENT.json", pointer_bytes)
    return manifest_key


def sec_facts_release() -> None:
    security = {
        "schema_version": "sec-terminal-snapshot-v1",
        "as_of": "2026-02-20",
        "cik": TSTA_CIK,
        "ticker": "TSTA",
        "fact_contract": "sec-pit-facts-v2",
        "source": "SEC Company Facts",
        "source_evidence": {
            "bucket": "fixture",
            "key": f"{CORPUS}/raw/sec/companyfacts/cik={TSTA_CIK}/companyfacts.json",
            "sha256": hashlib.sha256(
                (
                    TREE
                    / "filings"
                    / CORPUS
                    / f"raw/sec/companyfacts/cik={TSTA_CIK}/companyfacts.json"
                ).read_bytes()
            ).hexdigest(),
        },
        "unavailable": {},
        "facts": {
            "revenue": {
                "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                "unit": "USD",
                "value": "1200",
                "end": "2025-12-31",
                "start": "2025-01-01",
                "period_kind": "annual",
                "available_at": "2026-02-11T05:00:00+00:00",
                "availability_evidence": "DATE_ONLY",
                "accession_number": "0000000001-26-000010",
                "filed": "2026-02-10",
                "fact_id": "f" * 64,
                "superseded_fact_ids": [],
                "selection_reason": "latest_available_same_context; equal-value ties use source ID",
            },
            "net_income": {
                "concept": "NetIncomeLoss",
                "unit": "USD",
                "value": "210",
                "end": "2025-12-31",
                "start": "2025-01-01",
                "period_kind": "annual",
                "available_at": "2026-02-11T05:00:00+00:00",
                "availability_evidence": "DATE_ONLY",
                "accession_number": "0000000001-26-000010",
                "filed": "2026-02-10",
                "fact_id": "0" * 64,
                "superseded_fact_ids": [],
                "selection_reason": "latest_available_same_context; equal-value ties use source ID",
            },
        },
    }
    files = []
    for ticker in ("TSTA",):
        path = f"securities/{ticker}.json"
        digest = write_json("financial", f"{SEC_FACTS_PREFIX}/{path}", security)
        files.append({"path": path, "sha256": digest, "size_bytes": 10})
    source_facts = f"{SEC_FACTS_PREFIX}/source_facts/NOT_PUBLISHED.json"
    manifest = {
        "schema_version": "sec-terminal-snapshot-v1",
        "as_of": "2026-02-20",
        "content_sha256": SEC_FACTS_RELEASE,
        "prefix": SEC_FACTS_PREFIX,
        "source_corpus": CORPUS,
        "tickers": ["TSTA"],
        "files": files + [{"path": source_facts, "sha256": "9" * 64, "size_bytes": 10}],
    }
    write_json("financial", f"manifests/sec-facts/{SEC_FACTS_RELEASE}.json", manifest)


def valuation_packets() -> None:
    packet = {
        "schema_version": "ppe-valuation-packet-v1",
        "ticker": "TSTA",
        "cik": TSTA_CIK,
        "cutoff_date": "2026-03-01",
        "built_at": "2026-03-01T00:00:00+00:00",
        "source": {
            "artifact": (
                f"gold/serving/releases/{SERVING_RELEASE}/entities/"
                f"entity_sec_cik_{TSTA_CIK}/fundamentals/annual.json"
            )
        },
        "bridge": {
            "cash": {
                "value": 610.0,
                "unit": "USD",
                "period_end": "2025-12-31",
                "available_at": "2026-02-11",
                "provenance": {
                    "sec_source": {
                        "archive_key": f"sec/bulk/{BULK_SOURCE}/companyfacts.zip",
                        "archive_member": f"CIK{TSTA_CIK}.json",
                        "archive_member_crc32": "00000000",
                        "sha256": "1" * 64,
                    }
                },
            },
            "long_term_debt": {
                "value": 430.0,
                "unit": "USD",
                "period_end": "2025-12-31",
                "available_at": "2026-02-11",
            },
            "short_term_debt": {
                "value": 115.0,
                "unit": "USD",
                "period_end": "2025-12-31",
                "available_at": "2026-02-11",
            },
            "minority_interest": None,
            "preferred_equity": None,
            "other_nonoperating_assets": None,
        },
        "diluted_shares": {
            "value": 1000.0,
            "unit": "shares",
            "period_end": "2025-12-31",
            "available_at": "2026-02-11",
        },
        "interest_expense": None,
        "dividends": {},
        "historical": [
            {
                "period_end": "2025-12-31",
                "fields": {
                    "revenue": {"value": 1200.0, "unit": "USD"},
                    "ebit": {"value": 305.0, "unit": "USD"},
                    "net_income": {"value": 210.0, "unit": "USD"},
                    "d_and_a": None,
                    "capex": None,
                    "diluted_shares": {"value": 1000.0, "unit": "shares"},
                    "book_value": {"value": 900.0, "unit": "USD"},
                    "nwc": {"value": 100.0, "unit": "USD"},
                    "tax_rate": {"value": 0.2, "unit": "pure"},
                },
            }
        ],
    }
    digest = write_json(
        "financial", f"gold/valuation/releases/{PACKET_RELEASE}/companies/TSTA.json", packet
    )
    write_json(
        "financial",
        "control/valuation/CURRENT.json",
        {
            "schema_version": "ppe-valuation-index-v1",
            "companies": {
                "TSTA": {
                    "key": f"gold/valuation/releases/{PACKET_RELEASE}/companies/TSTA.json",
                    "sha256": digest,
                }
            },
        },
    )


def company_dataset_b() -> None:
    """Issuer B has no approved packet and no published release: its CIK comes from
    the per-company versioned dataset, and its facts from the bulk archive member."""
    version = "d" * 32
    write_json(
        "financial",
        f"datasets/sec/TSTB/versions/{version}/data.json",
        {
            "schema_version": 1,
            "dataset_id": "sec/TSTB",
            "cik": int(TSTB_CIK),
            "name": "FIXTURE ISSUER B",
            "tickers": ["TSTB"],
            "rows": [
                {"accession": "0000000002-26-000005", "form": "10-K", "filing_date": "2026-02-20"}
            ],
        },
    )
    write_json(
        "financial",
        f"datasets/sec/TSTB/versions/{version}/manifest.json",
        {
            "version_id": version,
            "version_number": 1,
            "dataset_id": "sec/TSTB",
            "layer": "bronze",
            "checksum_sha256": "0" * 64,
            "storage_uri": f"datasets/sec/TSTB/versions/{version}/data.json",
        },
    )


def bulk_archive() -> None:
    buffer = io.BytesIO()
    members = (
        (f"CIK{TSTB_CIK}.json", json.dumps(companyfacts_b(), sort_keys=True)),
        ("CIK0000000003.json", json.dumps({"cik": 3, "facts": {}}, sort_keys=True)),
    )
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            # A pinned timestamp keeps the committed fixture byte-stable; the
            # default would embed the build clock in the archive.
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)
    write("research", f"sec/bulk/{BULK_SOURCE}/companyfacts.zip", buffer.getvalue())
    write_json(
        "research", f"sec/bulk/{BULK_SOURCE}/manifest.json", {"source": BULK_SOURCE, "members": 2}
    )
    write_json(
        "research",
        "inventory/releases/fixture0000000000/manifest.json",
        {
            "inventory_id": "fixture0000000000",
            "library_count": 1,
            "table_count": 1,
            "source": "WRDS_METADATA",
        },
    )


def main() -> None:
    if TREE.exists():
        shutil.rmtree(TREE)
    # Only issuer A is in the filing corpus, so issuer B must fall back to the
    # private bulk archive member.
    write_json(
        "filings",
        f"{CORPUS}/raw/sec/companyfacts/cik={TSTA_CIK}/companyfacts.json",
        companyfacts_a(),
    )
    serving_release()
    sec_facts_release()
    bulk_archive()
    company_dataset_b()
    valuation_packets()
    print(
        json.dumps(
            {
                "tree": str(TREE),
                "files": sorted(str(p.relative_to(TREE)) for p in TREE.rglob("*") if p.is_file()),
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
