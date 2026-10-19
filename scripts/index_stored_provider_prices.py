"""Metadata-only index over 86 existing estate price files; no provider acquisition."""

import argparse
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))
from archive_stock_index import build, digest, encode, spans  # noqa: E402

BUCKET = "financial-system-datasets"
BASE = "estate/snapshot=20260904T000609Z-ab70e2cd318a/data/root=scott_brain_parquet_lake/"


class Remote(io.RawIOBase):
    def __init__(self, s3, key, size, etag):
        self.s3, self.key, self.size, self.etag, self.position = s3, key, size, etag, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, n, whence=0):
        self.position = n if whence == 0 else self.position + n if whence == 1 else self.size + n
        return self.position

    def read(self, n=-1):
        n = min(self.size - self.position, n if n >= 0 else self.size)
        if n <= 0:
            return b""
        r = self.s3.get_object(
            Bucket=BUCKET,
            Key=self.key,
            IfMatch=self.etag,
            Range=f"bytes={self.position}-{self.position + n - 1}",
        )
        with r["Body"] as body:
            raw = body.read(n + 1)
        if len(raw) != n:
            raise ValueError("ARCHIVE_RANGE_SIZE")
        self.position += n
        return raw


def run(s3, out):
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    labels = {}

    def listing(prefix):
        return [
            x
            for p in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix)
            for x in p.get("Contents", [])
            if x["Key"].endswith(".parquet")
        ]

    for item in listing(BASE + "security_master/"):
        h = s3.head_object(Bucket=BUCKET, Key=item["Key"])
        pf = pq.ParquetFile(Remote(s3, item["Key"], h["ContentLength"], h["ETag"]))
        for r in pf.read(columns=["security_id", "primary_ticker", "security_name"]).to_pylist():
            entry = labels.setdefault(str(r["security_id"]), dict(aliases=set(), names=set()))
            if r.get("primary_ticker"):
                entry["aliases"].add(r["primary_ticker"])
            if r.get("security_name"):
                entry["names"].add(r["security_name"])
    items = listing(BASE + "price_1d/")
    if len(items) != 86:
        raise ValueError("EXPECTED_SOURCE_OBJECT_COHORT_CHANGED")

    def scan(pair):
        i, item = pair
        key = item["Key"]
        provider = key.split("source_name=")[1].split("/")[0]
        sid = str(i)
        h = s3.head_object(Bucket=BUCKET, Key=key)
        pf = pq.ParquetFile(Remote(s3, key, h["ContentLength"], h["ETag"]))
        table = pf.read(columns=["security_id", "price_date"])
        runs = spans(
            table["security_id"].to_pylist(), table["price_date"].to_pylist(), provider, sid
        )
        return (
            sid,
            dict(
                key=key,
                etag=h["ETag"],
                bytes=h["ContentLength"],
                rows=pf.metadata.num_rows,
                provider=provider,
            ),
            runs,
        )

    entries = {}
    sources = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for sid, source, runs in pool.map(scan, enumerate(items)):
            sources[sid] = source
            for span in runs:
                key = span.pop("identity")
                security = span["security_id"]
                label = labels.get(security, {})
                e = entries.setdefault(
                    key,
                    dict(
                        provider=span["provider"],
                        security_id=security,
                        symbol=next(iter(sorted(label.get("aliases", []))), None),
                        aliases=sorted(label.get("aliases", [])),
                        name=next(iter(sorted(label.get("names", []))), None),
                        identity_basis="ARCHIVE_SECURITY_ID_STATIC_LABEL_NOT_HISTORICALLY_VERIFIED",
                        fragments=[],
                        normalized_rows=0,
                        date_min=span["date_min"],
                        date_max=span["date_max"],
                    ),
                )
                e["fragments"].append(span)
                e["normalized_rows"] += span["rows"]
                e["date_min"] = min(e["date_min"], span["date_min"])
                e["date_max"] = max(e["date_max"], span["date_max"])
    objects, pointer = build(entries, sources)

    def publish(pair):
        key, raw = pair
        try:
            s3.put_object(
                Bucket=BUCKET, Key=key, Body=raw, IfNoneMatch="*", ContentType="application/json"
            )
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") not in (
                "PreconditionFailed",
                "412",
            ):
                raise
        with s3.get_object(Bucket=BUCKET, Key=key)["Body"] as body:
            actual = body.read(len(raw) + 1)
        if digest(actual) != digest(raw):
            raise ValueError("INDEX_READBACK_FAILURE")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(publish, objects.items()))
    pointer.update(
        indexed_rows=sum(e["normalized_rows"] for e in entries.values()),
        security_count=len(entries),
        objects=len(objects),
        manifest_bytes=len(objects[pointer["key"]]),
    )
    (out / "pointer.json").write_bytes(encode(pointer))
    print(json.dumps(pointer), flush=True)
    return pointer


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--io-helpers", type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.io_helpers))
    from stock_backfill_io import r2_client

    run(r2_client(), a.output)
