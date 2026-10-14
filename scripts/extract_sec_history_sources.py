"""Extract five bounded public SEC members from the existing immutable archive."""

import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "deploy/cloudflare-query/src"))
from reconcile_financial_inventory import client

OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-financial-history")
CIKS = ["0000320193", "0000909832", "0001045810", "0001326801", "0001652044"]


def main():
    OUT.mkdir(mode=0o700, exist_ok=True)
    s = client()
    cfg = json.loads(
        (Path(__file__).parents[1] / "deploy/cloudflare-query/thin/wrangler.jsonc").read_text()
    )["vars"]

    def meta(ref):
        raw = s.get_object(Bucket="financial-system-datasets", Key=ref["key"])["Body"].read(1048577)
        assert len(raw) <= 1048576 and hashlib.sha256(raw).hexdigest() == ref["sha256"]
        return json.loads(raw)

    index = meta({"key": cfg["SEC_EXPANDED_INDEX_KEY"], "sha256": cfg["SEC_EXPANDED_INDEX_SHA256"]})
    source = index["source"]
    head = s.head_object(Bucket=source["bucket"], Key=source["key"])
    assert head["ETag"].strip('"') == source["etag"]
    lineage = []
    for cik in CIKS:
        entry = meta(index["shards"][cik[-2:]])[cik]
        assert (
            entry["compressed_bytes"] <= 8 * 1048576 and entry["uncompressed_bytes"] <= 24 * 1048576
        )

        def read(start, n):
            raw = s.get_object(
                Bucket=source["bucket"],
                Key=source["key"],
                Range=f"bytes={start}-{start + n - 1}",
                IfMatch=head["ETag"],
            )["Body"].read(n + 1)
            assert len(raw) == n
            return raw

        header = struct.unpack("<4s5H3I2H", read(entry["offset"], 30))
        assert header[0] == b"PK\x03\x04"
        compressed = read(entry["offset"] + 30 + header[-2] + header[-1], entry["compressed_bytes"])
        if entry["compression"] == 8:
            d = zlib.decompressobj(-15)
            raw = d.decompress(compressed, 24 * 1048576 + 1)
            assert d.eof and not d.unconsumed_tail and not d.unused_data
        else:
            assert entry["compression"] == 0
            raw = compressed
        assert len(raw) == entry["uncompressed_bytes"] and zlib.crc32(raw) == entry["crc"]
        company = json.loads(raw)
        assert str(company["cik"]).zfill(10) == cik
        path = OUT / (cik + ".companyfacts.json")
        path.write_bytes(raw)
        path.chmod(0o600)
        tags = company["facts"]["us-gaap"]
        lineage.append(
            {
                "cik": cik,
                "label": company["entityName"],
                "member_sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "archive": source,
                "etag": source["etag"],
                "entry": entry,
                "rights": "PUBLIC_SEC_ONLY",
            }
        )
        print(
            json.dumps(
                {
                    "cik": cik,
                    "bytes": len(raw),
                    "debt_concepts": [
                        k
                        for k in tags
                        if any(
                            w in k
                            for w in [
                                "DebtCurrent",
                                "DebtNoncurrent",
                                "ShortTermBorrow",
                                "CommercialPaper",
                            ]
                        )
                    ],
                    "revenue_concepts": [
                        k
                        for k in tags
                        if k
                        in [
                            "RevenueFromContractWithCustomerExcludingAssessedTax",
                            "SalesRevenueNet",
                            "Revenues",
                        ]
                    ],
                }
            ),
            flush=True,
        )
    (OUT / "source-lineage.json").write_text(json.dumps(lineage, indent=2) + "\n")


if __name__ == "__main__":
    main()
