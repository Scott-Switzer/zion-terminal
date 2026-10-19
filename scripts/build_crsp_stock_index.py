"""Build an immutable identity/span index over existing private CRSP parquet only.

Projects PERMNO only: no prices acquired, printed, downloaded, copied or republished.
Native dated identity is owner-private metadata. Output contains aggregates and hashes.
"""

import argparse
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))
from crsp_archive import build, catalog, digest, encode  # noqa: E402

BUCKET = "financial-data-wrds-private"


class RangeFile(io.RawIOBase):
    def __init__(self, s3, key, size, etag):
        self.s3, self.key, self.size, self.etag, self.position = s3, key, size, etag, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = (
            offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        )
        if self.position < 0:
            raise ValueError("NEGATIVE_SEEK")
        return self.position

    def read(self, n=-1):
        n = min(self.size - self.position, n if n >= 0 else self.size)
        if n <= 0:
            return b""
        response = self.s3.get_object(
            Bucket=BUCKET,
            Key=self.key,
            IfMatch=self.etag,
            Range=f"bytes={self.position}-{self.position + n - 1}",
        )
        with response["Body"] as body:
            raw = body.read(n + 1)
        if len(raw) != n:
            raise ValueError("CRSP_RANGE_SIZE")
        self.position += n
        return raw


def index(coverage_path, directory, s3, *, publish=False, workers=4):
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    coverage_raw = coverage_path.read_bytes()
    datasets = {d["dataset"]: d for d in json.loads(coverage_raw)["datasets"]}
    daily = datasets["crsp_a_stock__stkdlysecurityprimarydata"]
    names = datasets["crsp_a_stock__stocknames"]
    identity_rows = []
    for part in names["objects"]:
        response = s3.get_object(Bucket=BUCKET, Key=part["key"])
        with response["Body"] as body:
            raw = body.read(16 * 1048576 + 1)
        if len(raw) != part["bytes"] or digest(raw) != part["source_sha256"]:
            raise ValueError("CRSP_IDENTITY_HASH")
        identity_rows.extend(
            pq.read_table(
                io.BytesIO(raw),
                columns=["permno", "ticker", "comnam", "namedt", "nameenddt", "permco"],
            ).to_pylist()
        )
    entries = catalog(identity_rows)
    sources, locations = {}, {}

    def scan(item):
        number, part = item
        sid = str(number)
        # Resume only metadata produced from the exact source receipt.
        cache = directory / ("part-" + sid + ".json")
        if cache.exists():
            result = json.loads(cache.read_bytes())
            if (
                result["source"]["sha256"] == part["source_sha256"]
                and result["source"]["key"] == part["key"]
                and result.get("date_index") == "CALENDAR_YEAR_V1"
            ):
                return sid, result
        head = s3.head_object(Bucket=BUCKET, Key=part["key"])
        if head["ContentLength"] != part["bytes"]:
            raise ValueError("CRSP_SOURCE_SIZE")
        pf = pq.ParquetFile(RangeFile(s3, part["key"], part["bytes"], head["ETag"]))
        if pf.metadata.num_rows != part["rows"]:
            raise ValueError("CRSP_SOURCE_COUNT")
        date_i = pf.schema_arrow.names.index("dlycaldt")
        offset, spans, dates = 0, [], []
        for group in range(pf.metadata.num_row_groups):
            stats = pf.metadata.row_group(group).column(date_i).statistics
            lo, hi = (
                (str(stats.min), str(stats.max)) if stats and stats.has_min_max else (None, None)
            )
            dates.extend([lo, hi] if lo else [])
            table = pf.read_row_group(group, columns=["permno", "dlycaldt"])
            ids = table["permno"].to_numpy()
            years = pc.year(table["dlycaldt"]).to_numpy()
            native_dates = pc.cast(table["dlycaldt"], "date32").to_numpy()
            if table["permno"].null_count or table["dlycaldt"].null_count:
                raise ValueError("CRSP_NULL_IDENTITY_DATE")
            cuts = np.flatnonzero((ids[1:] != ids[:-1]) | (years[1:] != years[:-1])) + 1
            starts = np.concatenate(([0], cuts))
            ends = np.concatenate((cuts, [len(ids)]))
            for begin, end in zip(starts, ends):
                days = native_dates[begin:end]
                spans.append(
                    dict(
                        permno=int(ids[begin]),
                        offset=int(offset + begin),
                        rows=int(end - begin),
                        source=sid,
                        row_group=group,
                        date_min=str(days.min()),
                        date_max=str(days.max()),
                        calendar_year=str(years[begin]),
                        date_monotonic=bool(np.all(days[1:] >= days[:-1])),
                    )
                )
            offset += len(ids)

        source = dict(
            key=part["key"],
            sha256=part["source_sha256"],
            etag=head["ETag"],
            bytes=part["bytes"],
            rows=offset,
            date_min=min(dates) if dates else None,
            date_max=max(dates) if dates else None,
            hash_status="MANIFEST_DECLARED_RANGE_ETAG_GUARDED",
            dataset=daily["dataset"],
            release=daily["release"],
        )
        result = dict(source=source, spans=spans, date_index="CALENDAR_YEAR_V1")
        cache.write_bytes(encode(result))
        cache.chmod(0o600)
        return sid, result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for i, (sid, result) in enumerate(executor.map(scan, enumerate(daily["objects"]))):
            sources[sid] = result["source"]
            for span in result["spans"]:
                permno = str(span["permno"])
                fragment = {k: v for k, v in span.items() if k != "permno"}
                locations.setdefault(permno, []).append(fragment)
            if (i + 1) % 20 == 0:
                print(
                    json.dumps(
                        dict(
                            phase="identity_spans",
                            parts=i + 1,
                            total_parts=len(daily["objects"]),
                            securities=len(locations),
                        )
                    ),
                    flush=True,
                )
    indexed = sum(x["rows"] for v in locations.values() for x in v)
    if indexed != daily["rows"]:
        raise ValueError("CRSP_INDEX_COUNT")
    # A native price PERMNO missing stocknames is retained explicitly, never discarded.
    for permno, fragments in locations.items():
        entry = entries.setdefault(
            permno,
            dict(
                permno=int(permno),
                security_id="CRSP:PERMNO:" + permno,
                symbol="",
                name="",
                intervals=[],
                identity_basis="NATIVE_PERMNO_NO_STOCKNAMES_INTERVAL",
            ),
        )
        entry.update(
            normalized_rows=sum(x["rows"] for x in fragments),
            date_min=min(x["date_min"] for x in fragments if x["date_min"]),
            date_max=max(x["date_max"] for x in fragments if x["date_max"]),
        )
    for entry in entries.values():
        entry.setdefault("normalized_rows", 0)
        entry.setdefault("date_min", None)
        entry.setdefault("date_max", None)
    objects, pointer = build(entries, locations, sources, coverage_sha256=digest(coverage_raw))
    for key, raw in objects.items():
        if publish:
            try:
                s3.put_object(
                    Bucket=BUCKET,
                    Key=key,
                    Body=raw,
                    IfNoneMatch="*",
                    ContentType="application/json",
                )
            except Exception as error:
                if getattr(error, "response", {}).get("Error", {}).get("Code") not in (
                    "PreconditionFailed",
                    "412",
                ):
                    raise
            response = s3.get_object(Bucket=BUCKET, Key=key)
            with response["Body"] as body:
                actual = body.read(len(raw) + 1)
            if digest(actual) != digest(raw):
                raise ValueError("CRSP_PUBLICATION_READBACK")
    (directory / "pointer.json").write_bytes(
        encode(dict(pointer, bucket=BUCKET, published=publish))
    )
    (directory / "manifest.json").write_bytes(objects[pointer["key"]])
    (directory / "manifest.json").chmod(0o600)
    print(
        json.dumps(
            dict(
                pointer,
                bucket=BUCKET,
                published=publish,
                objects=len(objects),
                index_bytes=sum(map(len, objects.values())),
                source_parts=len(sources),
            )
        ),
        flush=True,
    )
    return pointer


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--io-helpers", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.io_helpers))
    from stock_backfill_io import r2_client

    index(args.coverage, args.output, r2_client(), publish=args.publish)
