"""Publish a tiny serving index over an existing R2 ZIP; no acquisition/extraction."""

import argparse
import configparser
import hashlib
import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SOURCE_BUCKET = "financial-data-wrds-private"
SOURCE_PREFIX = "sec/bulk/sec-nightly-20260924/"
DESTINATION = "financial-system-datasets"


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def build_objects(entries, ticker_snapshot, source):
    members, oversized = {}, 0
    for item in entries:
        match = re.fullmatch(r"CIK(\d{10})\.json", item["name"])
        if not match:
            continue
        if (
            item["uncompressed_bytes"] > 4 * 1048576
            or item["compressed_bytes"] > 2 * 1048576
            or item["compression"] not in (0, 8)
            or item["flags"] & 1
        ):
            oversized += 1
            continue
        members[match[1]] = item
    tickers, ambiguous = {}, set()
    for row in ticker_snapshot.values():
        cik, symbol = str(row["cik_str"]).zfill(10), row["ticker"].upper()
        if cik not in members or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,19}", symbol):
            continue
        if symbol in tickers and tickers[symbol] != cik:
            ambiguous.add(symbol)
        tickers[symbol] = cik
    for symbol in ambiguous:
        tickers.pop(symbol, None)
    identity = {
        "version": "zion-sec-expanded-index-v1",
        "source": source,
        "member_index_sha256": sha(encode(members)),
        "ticker_snapshot_sha256": sha(encode(ticker_snapshot)),
    }
    release = sha(encode(identity))
    prefix = f"gold/sec-expanded/releases/{release}/"
    objects, shards = {}, {}
    for suffix in sorted({cik[-2:] for cik in members}):
        payload = {cik: item for cik, item in members.items() if cik.endswith(suffix)}
        raw = encode(payload)
        key = prefix + "index/" + suffix + ".json"
        objects[key] = raw
        shards[suffix] = {
            "key": key,
            "sha256": sha(raw),
            "bytes": len(raw),
            "members": len(payload),
        }
    ticker_raw = encode(tickers)
    ticker_key = prefix + "tickers.json"
    objects[ticker_key] = ticker_raw
    manifest = {
        **identity,
        "release_id": release,
        "shards": shards,
        "tickers": {"key": ticker_key, "sha256": sha(ticker_raw), "bytes": len(ticker_raw)},
        "counts": {
            "indexed_filers": len(members),
            "oversized_or_unsupported": oversized,
            "mapped_ticker_symbols": len(tickers),
            "mapped_unique_issuers": len(set(tickers.values())),
            "ambiguous_tickers_excluded": len(ambiguous),
        },
        "identity_policy": (
            "stored SEC ticker snapshot; date and listed-security classification not certified"
        ),
        "archive_sha256_status": "DECLARED_NOT_FULLY_REHASHED",
        "member_integrity": "ETag-pinned ranges, ZIP CRC/size and computed member SHA-256",
    }
    key = prefix + "manifest.json"
    raw = encode(manifest)
    objects[key] = raw
    return objects, {"key": key, "sha256": sha(raw), "counts": manifest["counts"]}


class RangeFile(io.RawIOBase):
    def __init__(self, client, key, head):
        self.client, self.key, self.head = client, key, head
        self.position, self.bytes_read = 0, 0

    def seek(self, offset, whence=0):
        self.position = (
            offset
            if whence == 0
            else self.position + offset
            if whence == 1
            else self.head["ContentLength"] + offset
        )
        if not 0 <= self.position <= self.head["ContentLength"]:
            raise ValueError("invalid source range")
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        if size == -1:
            size = self.head["ContentLength"] - self.position
        if size < 0 or size > 4 * 1048576:
            raise ValueError("metadata read budget exceeded")
        size = min(size, self.head["ContentLength"] - self.position)
        parts = []
        while size:
            n = min(size, 1048576)
            response = self.client.get_object(
                Bucket=SOURCE_BUCKET,
                Key=self.key,
                Range=f"bytes={self.position}-{self.position + n - 1}",
                IfMatch=self.head["ETag"],
            )
            raw = response["Body"].read(n + 1)
            response["Body"].close()
            if len(raw) != n:
                raise ValueError("source range mismatch")
            parts.append(raw)
            self.position += n
            self.bytes_read += n
            size -= n
            if self.bytes_read > 8 * 1048576:
                raise ValueError("central directory budget exceeded")
        return b"".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker-snapshot", required=True)
    parser.add_argument("--output-metadata", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    import boto3

    profile = configparser.ConfigParser(interpolation=None)
    profile.read(Path.home() / ".config/rclone/rclone.conf")
    remote = profile["r2"]
    client = boto3.client(
        "s3",
        endpoint_url=remote["endpoint"],
        aws_access_key_id=remote["access_key_id"],
        aws_secret_access_key=remote["secret_access_key"],
        region_name="auto",
    )
    source_response = client.get_object(Bucket=SOURCE_BUCKET, Key=SOURCE_PREFIX + "manifest.json")
    source_raw = source_response["Body"].read(65537)
    if len(source_raw) > 65536:
        raise ValueError("source manifest limit")
    source_manifest = json.loads(source_raw)
    key = SOURCE_PREFIX + "companyfacts.zip"
    head = client.head_object(Bucket=SOURCE_BUCKET, Key=key)
    if head["ContentLength"] != source_manifest["objects"]["companyfacts"]["bytes"]:
        raise ValueError("source size mismatch")
    reader = RangeFile(client, key, head)
    with zipfile.ZipFile(reader) as archive:
        entries = [
            {
                "name": i.filename,
                "compression": i.compress_type,
                "flags": i.flag_bits,
                "crc": i.CRC,
                "compressed_bytes": i.compress_size,
                "uncompressed_bytes": i.file_size,
                "offset": i.header_offset,
            }
            for i in archive.infolist()
        ]
    ticker_path = Path(args.ticker_snapshot)
    if ticker_path.stat().st_size > 2 * 1048576:
        raise ValueError("ticker snapshot budget")
    source = {
        "bucket": SOURCE_BUCKET,
        "key": key,
        "etag": head["ETag"].strip('"'),
        "bytes": head["ContentLength"],
        "snapshot_date": "2026-09-24",
        "declared_sha256": source_manifest["objects"]["companyfacts"]["sha256"],
        "manifest_sha256": sha(source_raw),
    }
    objects, result = build_objects(entries, json.loads(ticker_path.read_text()), source)
    if args.publish:

        def write(item):
            key, raw = item
            try:
                client.put_object(
                    Bucket=DESTINATION,
                    Key=key,
                    Body=raw,
                    ContentType="application/json",
                    IfNoneMatch="*",
                    Metadata={"sha256": sha(raw)},
                )
            except client.exceptions.ClientError as exc:
                if exc.response["Error"]["Code"] not in ("PreconditionFailed", "412"):
                    raise
            observed = client.get_object(Bucket=DESTINATION, Key=key)["Body"].read(len(raw) + 1)
            if sha(observed) != sha(raw):
                raise ValueError("index readback mismatch")

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, objects.items()))
    result.update(
        published=args.publish,
        index_objects=len(objects),
        source_metadata_bytes_read=reader.bytes_read,
        provider_requests=0,
    )
    Path(args.output_metadata).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
