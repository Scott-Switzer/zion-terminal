"""Bounded existing Alpaca projection. Writes protected local files, never provider/cloud writes.

Never print observations: stdout contains aggregate receipt only.
"""

import argparse
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

BUCKET = "financial-system-datasets"
DOC = "https://docs.alpaca.markets/us/reference/stockbars"
RIGHTS = "PRIVATE_OWNER_ALPACA_RESEARCH_ONLY"
DEFAULT_SYMBOLS = (
    "AAPL,MSFT,NVDA,JPM,BAC,GS,MS,V,MA,UNH,JNJ,PFE,MRK,KO,PEP,PG,WMT,NKE,HD,LOW,DIS,INTC,CSCO"
)

FIELDS = ("open", "high", "low", "close", "volume", "vwap", "transactions")


def encode(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def number(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("INVALID_NUMERIC")
    d = Decimal(str(value))
    if not d.is_finite():
        raise ValueError("INVALID_NUMERIC")
    return str(d)


def currency_contract(manifest):
    url = urlsplit(manifest.get("endpoint", ""))
    query = parse_qs(url.query, keep_blank_values=True)
    if (
        url.scheme != "https"
        or url.netloc != "data.alpaca.markets"
        or url.path != "/v2/stocks/bars"
        or manifest.get("asof") != "-"
        or manifest.get("adjustment") != "raw"
        or manifest.get("feed") != "sip"
        or query.get("asof") != ["-"]
        or query.get("adjustment") != ["raw"]
        or query.get("feed") != ["sip"]
    ):
        raise ValueError("UNVERIFIED_REQUEST_CONTRACT")
    currency = query.get("currency")
    if currency not in (None, ["USD"]):
        raise ValueError("UNSUPPORTED_REQUEST_CURRENCY")
    return dict(
        currency="USD",
        basis="DOCUMENTED_REQUEST_DEFAULT" if currency is None else "EXPLICIT_REQUEST_CURRENCY",
        documentation=DOC,
        documentation_reviewed_at="2026-10-14",
        request_currency=currency,
        historical_symbol_mapping="DISABLED_ASOF_MINUS",
    )


def project(symbol, rows, currency_proofs, raw_verified=False):
    grouped = defaultdict(dict)
    associations = set()
    for row in rows:
        if (
            row["provider_symbol"] != symbol
            or row["feed"] != "SIP"
            or row["price_basis"] != "RAW"
            or row["adjustment_method"] != "NONE"
        ):
            raise ValueError("SOURCE_CONTRACT_MISMATCH")
        values = {name: number(row.get(name)) for name in FIELDS}
        if any(values[x] is None for x in ("open", "high", "low", "close", "volume")):
            raise ValueError("MISSING_REQUIRED_BAR")
        if (
            min(Decimal(values[x]) for x in ("open", "high", "low", "close")) <= 0
            or Decimal(values["volume"]) < 0
            or Decimal(values["low"]) > min(Decimal(values["open"]), Decimal(values["close"]))
            or Decimal(values["high"]) < max(Decimal(values["open"]), Decimal(values["close"]))
        ):
            raise ValueError("INVALID_OHLC")
        day = str(row["session_date"])
        source = dict(
            bucket=BUCKET,
            key=row["source_object_key"],
            sha256=row["source_hash"],
            provider_symbol=symbol,
            session_date=day,
            feed="SIP",
            price_basis="RAW",
            adjustment_method="NONE",
        )
        source_id = sha(encode(source))
        proof = currency_proofs.get(row["source_hash"])
        value = dict(
            session_date=day,
            **values,
            source_currency=row.get("currency", "UNKNOWN"),
            currency=proof["currency"] if proof else "UNKNOWN",
            currency_evidence=proof,
            market_available_at=row.get("market_available_at"),
            historical_availability="NOT_ESTABLISHED",
        )
        # Retrieval times and current UUID do not decide which financial bar wins.
        fingerprint = sha(encode({k: v for k, v in value.items() if k != "currency_evidence"}))
        entry = grouped[day].setdefault(fingerprint, dict(value=value, sources={}))
        entry["sources"][source_id] = dict(
            source_record_id=source_id, **source, retrieved_at=row.get("retrieved_at")
        )
        if row.get("alpaca_asset_uuid"):
            associations.add(row["alpaca_asset_uuid"])
    observations, gaps = [], []
    source_records, source_pages = {}, {}
    for day, variants in sorted(grouped.items()):
        sources = {k: v for entry in variants.values() for k, v in entry["sources"].items()}
        for source_id, source in sources.items():
            page = source["sha256"]
            source_pages[page] = {k: source[k] for k in ("bucket", "key", "sha256", "retrieved_at")}
            source_pages[page]["currency_evidence"] = currency_proofs.get(page)
            source_records[source_id] = dict(
                source_page_sha256=page, session_date=day, provider_symbol=symbol, feed="SIP"
            )
        if len(variants) != 1:
            gaps.append(
                dict(
                    session_date=day,
                    reason="CONFLICTING_SOURCE_BARS",
                    source_record_ids=sorted(sources),
                )
            )
        else:
            observations.append(
                dict(
                    next(iter(variants.values()))["value"],
                    source_record_ids=sorted(sources),
                )
            )
    return dict(
        schema_version="owner-market-history-v1",
        provider_symbol=symbol,
        security_id="alpaca:sip:source-symbol:" + symbol,
        rights=RIGHTS,
        feed="SIP",
        price_basis="RAW",
        adjustment_method="NONE",
        observations=observations,
        gaps=gaps,
        source_records=source_records,
        source_pages=source_pages,
        numeric_representation=(
            "ORIGINAL_RAW_DECIMAL_TOKEN_VERIFIED"
            if raw_verified
            else "FLOAT64_PROJECTION_UNCERTIFIED"
        ),
        historical_identity_status="NOT_ESTABLISHED",
        historical_cik_bridge=None,
        current_asset_associations=[
            dict(
                alpaca_asset_uuid=x,
                historical_validity="UNKNOWN",
                association_basis="CURRENT_SOURCE_SNAPSHOT",
            )
            for x in sorted(associations)
        ],
        corporate_actions=dict(status="NOT_INTEGRATED_RIGHTS_AND_IDENTITY_UNVERIFIED"),
        valuation_eligibility="BLOCKED_DATED_SECURITY_ISSUER_BRIDGE_AND_ACTIONS",
        availability_status="NOT_ESTABLISHED",
        internal_session_gaps="NOT_INFERRED_WITHOUT_CALENDAR",
    )


def prepare(inventory, out, symbols, s3, maximum_output_bytes=5 * 1024**2):
    import pyarrow.parquet as pq

    if maximum_output_bytes not in (5 * 1024**2, 8 * 1024**2):
        raise ValueError("OUTPUT_BUDGET_NOT_APPROVED")
    inventory_raw = inventory.read_bytes()
    datasets = json.loads(inventory_raw)["alpaca"]["datasets"]
    if len(datasets) > 20 or sum(x["bytes"] for x in datasets) > 40 * 1024**2:
        raise ValueError("TOTAL_INPUT_BUDGET")
    if not 1 <= len(symbols) <= 30 or any(
        not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", x) for x in symbols
    ):
        raise ValueError("COHORT_LIMIT")
    selected = {x: [] for x in symbols}
    proofs, source_refs = {}, []
    metadata_reads = 0

    def read(key, limit):
        nonlocal metadata_reads
        if (
            not key.startswith(
                ("normalized/alpaca/source-raw-daily-v1/", "raw/alpaca/equity-bars/")
            )
            or ".." in key
        ):
            raise ValueError("KEY_BLOCKED")
        response = s3.get_object(Bucket=BUCKET, Key=key)
        with response["Body"] as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit or len(raw) != response["ContentLength"]:
            raise ValueError("INPUT_SIZE_MISMATCH")
        return raw

    physical = 0
    total_input_bytes = 0
    for dataset in datasets:
        if dataset["bytes"] > 8 * 1024**2:
            raise ValueError("OBJECT_LIMIT")
        raw = read(dataset["normalized_key"], 8 * 1024**2)
        if len(raw) != dataset["bytes"] or sha(raw) != dataset["sha256"]:
            raise ValueError("SOURCE_HASH_MISMATCH")
        source_refs.append(
            dict(bucket=BUCKET, key=dataset["normalized_key"], bytes=len(raw), sha256=sha(raw))
        )
        total_input_bytes += len(raw)
        file = pq.ParquetFile(io.BytesIO(raw))
        if file.metadata.num_rows != dataset["normalized_rows"]:
            raise ValueError("SOURCE_ROWCOUNT_MISMATCH")
        for batch in file.iter_batches(batch_size=10000):
            physical += batch.num_rows
            for row in batch.to_pylist():
                if row["provider_symbol"] not in selected or row["feed"] != "SIP":
                    continue
                h = row["source_hash"]
                if h not in proofs:
                    if metadata_reads >= 200:
                        raise ValueError("METADATA_REQUEST_BUDGET")
                    key = row["source_object_key"].removesuffix(".json") + ".manifest.json"
                    metadata_raw = read(key, 256 * 1024)
                    metadata_reads += 1
                    manifest = json.loads(metadata_raw)
                    if (
                        manifest["storage_key"] != row["source_object_key"]
                        or manifest["content_sha256"] != h
                    ):
                        raise ValueError("RAW_MANIFEST_MISMATCH")
                    proof = currency_contract(manifest)
                    proof["request_manifest"] = dict(
                        bucket=BUCKET, key=key, sha256=sha(metadata_raw), bytes=len(metadata_raw)
                    )
                    proofs[h] = proof
                selected[row["provider_symbol"]].append(row)
    # Recover exact original numeric tokens; never infer decimals from binary floats.
    by_page = defaultdict(list)
    for rows in selected.values():
        for row in rows:
            by_page[(row["source_object_key"], row["source_hash"])].append(row)
    for (key, expected_hash), rows in by_page.items():
        raw = read(key, 8 * 1024**2)
        total_input_bytes += len(raw)
        if total_input_bytes > 100 * 1024**2 or sha(raw) != expected_hash:
            raise ValueError("RAW_INPUT_BUDGET_OR_HASH")
        native = json.loads(raw, parse_float=Decimal)["bars"]
        needed = {r["provider_symbol"] for r in rows}
        indexed = {}
        for symbol in needed:
            for bar in native.get(symbol, []):
                day = (
                    datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
                    .astimezone(ZoneInfo("America/New_York"))
                    .date()
                    .isoformat()
                )
                item = (symbol, day)
                if item in indexed:
                    raise ValueError("DUPLICATE_RAW_BAR")
                indexed[item] = bar
        for row in rows:
            bar = indexed[(row["provider_symbol"], str(row["session_date"]))]
            for field, native_field in zip(FIELDS, ("o", "h", "l", "c", "v", "vw", "n")):
                value = bar.get(native_field)
                prior = row.get(field)
                if prior is not None and (value is None or float(value) != float(prior)):
                    raise ValueError("NORMALIZED_RAW_NUMERIC_MISMATCH")
                # Existing normalizer explicitly marks invalid optional values missing.
                row[field] = value if prior is not None else None
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    directory = {}
    total_bytes = 0
    for symbol, rows in sorted(selected.items()):
        data = project(symbol, rows, proofs, raw_verified=True)
        raw = encode(data)
        total_bytes += len(raw)
        if len(raw) > 1024**2 or total_bytes > maximum_output_bytes:
            raise ValueError("OUTPUT_BUDGET")
        name = symbol + ".json"
        (out / name).write_bytes(raw)
        (out / name).chmod(0o600)
        directory[symbol] = dict(
            local_file=name,
            sha256=sha(raw),
            bytes=len(raw),
            observations=len(data["observations"]),
            conflicts=len(data["gaps"]),
            first_session=data["observations"][0]["session_date"] if data["observations"] else None,
            last_session=data["observations"][-1]["session_date"] if data["observations"] else None,
        )
    identity = dict(
        schema_version="owner-market-release-v1",
        rights=RIGHTS,
        provider="ALPACA",
        feed="SIP",
        terms_reference="https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf",
        source_inventory_sha256=sha(inventory_raw),
        sources=source_refs,
        symbols=directory,
        source_contracts=dict(
            provider="ALPACA",
            feed="SIP",
            price_basis="RAW",
            adjustment_method="NONE",
            currency_documentation=DOC,
            no_provider_requests=True,
            no_cloud_writes=True,
            no_licensed_rows=True,
            no_model_rows=True,
            current_uuid_not_historical_identity=True,
        ),
    )
    root = dict(identity, release_id=sha(encode(identity)))
    (out / "manifest.json").write_bytes(encode(root))
    (out / "manifest.json").chmod(0o600)
    receipt = dict(
        status="PASS",
        release_id=root["release_id"],
        physical_rows_verified=physical,
        selected_symbols=len(symbols),
        symbols_with_history=sum(bool(x["observations"]) for x in directory.values()),
        observations=sum(x["observations"] for x in directory.values()),
        conflicting_sessions=sum(x["conflicts"] for x in directory.values()),
        input_bytes=total_input_bytes,
        raw_page_gets=len(by_page),
        local_payload_bytes=total_bytes,
        metadata_gets=metadata_reads,
        parquet_gets=len(datasets),
        provider_requests=0,
        cloud_writes=0,
        source_price_rows_printed=0,
        manifest_sha256=sha(encode(root)),
    )
    (out / "receipt.json").write_bytes(encode(receipt))
    (out / "receipt.json").chmod(0o600)
    return receipt


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    p.add_argument("--maximum-output-mib", type=int, choices=(5, 8), default=5)
    args = p.parse_args()
    from reconcile_financial_inventory import client

    try:
        print(
            json.dumps(
                prepare(
                    args.inventory,
                    args.output,
                    args.symbols.split(","),
                    client(),
                    maximum_output_bytes=args.maximum_output_mib * 1024**2,
                )
            )
        )
    except Exception as exc:
        # Avoid arbitrary provider/library errors leaking payload fragments.
        print(json.dumps(dict(status="FAIL", error_type=type(exc).__name__)))
        raise SystemExit(1) from None
