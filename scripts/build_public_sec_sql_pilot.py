"""Extract a tiny, public SEC pilot from stored ETag-pinned ZIP members. No licensed rows."""

import hashlib
import json
import struct
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "deploy/cloudflare-query/src"))
from reconcile_financial_inventory import client
from sec_expanded import decode_member


def main():
    out = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/unified-financial-foundation")
    s = client()
    config = json.loads(
        (Path(__file__).parents[1] / "deploy/cloudflare-query/thin/wrangler.jsonc").read_text()
    )["vars"]

    def metadata(ref):
        raw = s.get_object(Bucket="financial-system-datasets", Key=ref["key"])["Body"].read(1048576)
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
        return json.loads(raw)

    index = metadata(
        {"key": config["SEC_EXPANDED_INDEX_KEY"], "sha256": config["SEC_EXPANDED_INDEX_SHA256"]}
    )
    source = index["source"]
    head = s.head_object(Bucket=source["bucket"], Key=source["key"])
    assert head["ETag"].strip('"') == source["etag"]
    identities = []
    facts = []
    members = []
    for cik in ["0001543151", "0001477333"]:
        entry = metadata(index["shards"][cik[-2:]])[cik]

        def read(start, length):
            return s.get_object(
                Bucket=source["bucket"],
                Key=source["key"],
                Range=f"bytes={start}-{start + length - 1}",
                IfMatch=head["ETag"],
            )["Body"].read(length + 1)

        header = struct.unpack("<4s5H3I2H", read(entry["offset"], 30))
        offset = entry["offset"] + 30 + header[-2] + header[-1]
        raw = decode_member(read(offset, entry["compressed_bytes"]), entry)
        company = json.loads(raw, parse_float=Decimal)
        assert str(company["cik"]).zfill(10) == cik
        member_sha = hashlib.sha256(raw).hexdigest()
        members.append(
            {
                "cik": cik,
                "sha256": member_sha,
                "public_source": "SEC_COMPANYFACTS",
                "bytes": len(raw),
            }
        )
        identities.append(
            {
                "cik": cik,
                "entity_id": "sec:cik:" + cik,
                "current_label": company["entityName"],
                "identity_role": "ISSUER_CIK_ANCHOR_NOT_HISTORICAL_TICKER_MAPPING",
            }
        )
        concept = "RevenueFromContractWithCustomerExcludingAssessedTax"
        rows = company["facts"]["us-gaap"][concept]["units"]["USD"]
        group = {}
        for row in rows:
            if row.get("form") not in ("10-K", "10-K/A") or "start" not in row:
                continue
            days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
            available = (
                datetime.combine(
                    date.fromisoformat(row["filed"]) + timedelta(days=1),
                    time.min,
                    ZoneInfo("America/New_York"),
                )
                .astimezone(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            if not 330 <= days <= 380 or available > "2026-01-01T00:00:00Z":
                continue
            key = (row["start"], row["end"])
            group.setdefault(key, []).append((available, row))
        selected = []
        target_contexts = sorted(group, key=lambda key: key[1])[-3:]
        for start, end in target_contexts:
            versions = group[(start, end)]
            for availability in {a for a, _ in versions}:
                assert (
                    len({str(Decimal(str(r["val"]))) for a, r in versions if a == availability})
                    == 1
                ), "ambiguous same-availability source revisions"
            seen = set()
            for available, row in versions:
                observation_id = hashlib.sha256(
                    (cik + concept + "USD" + json.dumps(row, sort_keys=True, default=str)).encode()
                ).hexdigest()
                if observation_id in seen:
                    continue
                seen.add(observation_id)
                selected.append(
                    {
                        "cik": cik,
                        "metric": "customer_contract_revenue_excluding_assessed_tax",
                        "mapping_version": "zion-financial-mapping-v1",
                        "concept": "us-gaap:" + concept,
                        "basis": "NATIVE_US_GAAP_CONCEPT_EXACT",
                        "unit": "USD",
                        "currency": "USD",
                        "period_start": start,
                        "period_end": end,
                        "available_at": available,
                        "value_decimal": str(Decimal(str(row["val"]))),
                        "accession": row["accn"],
                        "member_sha256": member_sha,
                        "source_observation_id": observation_id,
                        "availability_policy": "DATE_ONLY_NEXT_DAY_ET_V1",
                        "layer": "NORMALIZED_RESEARCH",
                        "quality": "SOURCE_UNVALIDATED",
                    }
                )
        facts.extend(selected)
    payload = {
        "rights": "PUBLIC_SEC_ONLY",
        "identities": identities,
        "facts": facts,
        "revision_policy": (
            "FILTER_AVAILABILITY_BEFORE_RANKING;"
            "ALL_SELECTED_CONTEXT_REVISIONS_RETAINED;CONFLICTS_REJECTED"
        ),
        "source_members": members,
        "archive_etag": source["etag"],
        "archive_full_sha_status": "DECLARED_NOT_REHASHED",
        "as_of": "2026-01-01T00:00:00Z",
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    p = out / "public-sec-pilot-input.json"
    p.write_bytes(raw)
    p.chmod(0o600)
    print(
        json.dumps(
            {
                "public_issuers": len(identities),
                "public_facts": len(facts),
                "input_sha256": hashlib.sha256(raw).hexdigest(),
                "licensed_rows_read": 0,
                "provider_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
