"""Publish only bounded approved private research artifacts; never moves CURRENT/configuration."""

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

BUCKET = "financial-system-datasets"
PREFIX = "gold/unified-research/releases/"
RIGHTS = "PRIVATE_OWNER_ALPACA_RESEARCH_ONLY"
FINANCIAL_RELEASE = "b39a670bad0670449256e59d38bf6b9600503449baded29c25e41d5fb368c6bb"


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def public_sec(data):
    if data.get("rights") != "PUBLIC_SEC_ONLY" or data.get("licensed_sources_allowed") not in (
        False,
        None,
    ):
        raise ValueError("PUBLIC_SEC_POLICY_REQUIRED")


def ref(key, raw):
    if not key.startswith(PREFIX) or ".." in key or "CURRENT" in key:
        raise ValueError("PUBLICATION_KEY_BLOCKED")
    return dict(key=key, sha256=sha(raw), bytes=len(raw))


def read_verified(s3, reference, limit):
    if reference["bytes"] > limit:
        raise ValueError("SOURCE_READ_BUDGET")
    response = s3.get_object(Bucket=BUCKET, Key=reference["key"])
    with response["Body"] as body:
        raw = body.read(limit + 1)
    if (
        len(raw) != reference["bytes"]
        or len(raw) != response["ContentLength"]
        or sha(raw) != reference["sha256"]
    ):
        raise ValueError("SOURCE_REFERENCE_MISMATCH")
    return raw


class Publisher:
    def __init__(self, s3):
        self.s3 = s3
        self.bytes = 0
        self.creates = 0
        self.readbacks = 0
        self.reserved_bytes = 0
        self.existing_objects = 0

    def write(self, key, raw):
        reference = ref(key, raw)
        if self.creates >= 100 or max(self.bytes, self.reserved_bytes) + len(raw) > 12 * 1024**2:
            raise ValueError("PUBLICATION_BUDGET")
        created = True
        try:
            self.s3.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=raw,
                IfNoneMatch="*",
                ContentType="application/json" if key.endswith(".json") else "text/plain",
                Metadata={"sha256": reference["sha256"]},
            )
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") not in (
                "PreconditionFailed",
                "412",
            ):
                raise RuntimeError("R2_CREATE_FAILED") from None
            created = False
        self.creates += 1
        self.reserved_bytes += len(raw)
        if created:
            self.bytes += len(raw)
        else:
            self.existing_objects += 1
        if read_verified(self.s3, reference, 2 * 1024**2) != raw:
            raise ValueError("CREATE_READBACK_MISMATCH")
        self.readbacks += 1
        return reference


def validate_market(data, symbol):
    if (
        data.get("rights") != RIGHTS
        or data.get("provider_symbol") != symbol
        or data.get("schema_version") != "owner-market-history-v1"
        or data.get("numeric_representation") != "ORIGINAL_RAW_DECIMAL_TOKEN_VERIFIED"
        or data.get("historical_cik_bridge") is not None
        or data.get("feed") != "SIP"
        or data.get("price_basis") != "RAW"
        or data.get("adjustment_method") != "NONE"
    ):
        raise ValueError("OWNER_PAYLOAD_POLICY_REQUIRED")
    for page_hash, page in data["source_pages"].items():
        if (
            page.get("bucket") != BUCKET
            or page.get("sha256") != page_hash
            or not page.get("key", "").startswith("raw/alpaca/equity-bars/")
            or ".." in page["key"]
        ):
            raise ValueError("OWNER_SOURCE_PATH_BLOCKED")
    for record in data["source_records"].values():
        if record.get("source_page_sha256") not in data["source_pages"]:
            raise ValueError("OWNER_SOURCE_LINEAGE_INCOMPLETE")
    for observation in data["observations"]:
        if observation.get("market_available_at") is not None:
            raise ValueError("OWNER_HISTORICAL_AVAILABILITY_UNSUPPORTED")
        if not observation.get("source_record_ids") or any(
            value not in data["source_records"] for value in observation["source_record_ids"]
        ):
            raise ValueError("OWNER_SOURCE_LINEAGE_INCOMPLETE")


def local_verified(path, reference):
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError("LOCAL_INPUT_OBJECT_BUDGET")
    raw = path.read_bytes()
    if len(raw) != reference["bytes"] or sha(raw) != reference["sha256"]:
        raise ValueError("LOCAL_REFERENCE_MISMATCH")
    return raw


def point_join_gate(identity, market_data, companion_data, resolver, minimum):
    joined, gaps = {}, []
    candidates = sorted(
        {
            (r["symbol"], r["observed_on"])
            for r in identity["identity_anchors"]
            if r.get("symbol") in market_data
        }
    )
    for symbol, on_date in candidates:
        try:
            anchor = resolver(identity["identity_anchors"], symbol, on_date)
        except ValueError:
            gaps.append(dict(symbol=symbol, date=on_date, reason="IDENTITY_UNRESOLVED"))
            continue
        cik = anchor["cik"]
        if cik not in companion_data:
            continue
        if not any(row["session_date"] == on_date for row in market_data[symbol]["observations"]):
            gaps.append(dict(symbol=symbol, date=on_date, reason="EXACT_SOURCE_PRICE_MISSING"))
            continue
        companion = companion_data[cik]
        cutoff = on_date + "T00:00:00Z"
        proof = companion.get("historical_reconciliation", {}).get(cutoff, {})
        if (
            proof.get("as_of") != cutoff
            or proof.get("member_sha256") != companion["member_sha256"]
            or proof.get("registry_sha256") != companion["registry_sha256"]
            or not proof.get("checks")
            or any(r.get("status") != "PASS" for r in proof["checks"])
        ):
            gaps.append(dict(symbol=symbol, date=on_date, reason="EXACT_HISTORICAL_PROOF_MISSING"))
            continue
        joined[cik] = dict(
            cik=cik,
            symbol=symbol,
            date=on_date,
            financial_cutoff=cutoff,
            qualification="RETROSPECTIVE_POINT_JOIN_NOT_INTRADAY_PIT",
        )
    if len(joined) < minimum:
        raise ValueError("MINIMUM_REAL_POINT_JOINS_NOT_MET")
    return sorted(joined.values(), key=lambda r: r["cik"]), gaps


def publish(
    base,
    financial_publication,
    repo,
    output,
    s3,
    owner_projection=None,
    certification_dir=None,
    expected_companies=20,
    minimum_point_joins=0,
    identity_file=None,
):
    financial = json.loads(financial_publication.read_bytes())
    if financial.get("status") != "PASS" or financial.get("release_id") != FINANCIAL_RELEASE:
        raise ValueError("FINANCIAL_RELEASE_NOT_APPROVED")
    financial_root = json.loads(read_verified(s3, financial["root"], 128 * 1024))
    public_sec(financial_root)
    if financial_root["release_id"] != FINANCIAL_RELEASE:
        raise ValueError("FINANCIAL_RELEASE_DRIFT")
    if not 20 <= expected_companies <= 40 or not 0 <= minimum_point_joins <= expected_companies:
        raise ValueError("COHORT_BUDGET_NOT_APPROVED")
    certification = certification_dir or base / "certification"
    summary_raw = (certification / "companion-summary.json").read_bytes()
    summary = json.loads(summary_raw)
    if (
        summary.get("companies_checked") != expected_companies
        or len(summary.get("companies", [])) != summary["companies_checked"]
        or summary.get("status") not in ("BUILT_LOCAL_PUBLIC_SEC_COMPANIONS", "PASS")
        or summary.get("new_provider_acquisition") is not False
    ):
        raise ValueError("CERTIFICATION_GATE_FAILED")
    files = {}
    companions = {}
    companion_data = {}
    shards = {}
    for record in summary["companies"]:
        cik = record["cik"]
        if (
            not re.fullmatch(r"\d{10}", cik)
            or cik in companions
            or record.get("identity_failures") != 0
        ):
            raise ValueError("CERTIFICATION_COHORT_INVALID")
        raw = local_verified(certification / (cik + ".companion.json"), record)
        data = json.loads(raw)
        public_sec(data)
        companion_data[cik] = data
        if data["cik"] != cik or data["registry_sha256"] != financial_root["registry_sha256"]:
            raise ValueError("CERTIFICATION_SCHEMA_DRIFT")
        suffix = cik[-2:]
        if suffix not in shards:
            shards[suffix] = json.loads(
                read_verified(s3, financial_root["shards"][suffix], 2 * 1024**2)
            )
        item = shards[suffix][cik]
        if item["materialized"]["member_sha256"] != data["member_sha256"]:
            raise ValueError("CERTIFICATION_MEMBER_DRIFT")
        actual_source_release = item["materialized"].get("source_release_id")
        if (
            actual_source_release not in financial_root["source_releases"]
            or data.get("financial_serving_release_id") != FINANCIAL_RELEASE
            or data.get("source_materialization_release_id") != actual_source_release
        ):
            raise ValueError("CERTIFICATION_RELEASE_BINDING_MISMATCH")
        name = "certifications/" + cik + ".json"
        files[name] = raw
        companions[cik] = dict(
            name=name,
            member_sha256=data["member_sha256"],
            registry_sha256=data["registry_sha256"],
            build_context_release_id=data["build_context_release_id"],
            source_materialization_release_id=item["materialized"].get("source_release_id"),
        )
    identity_raw = (identity_file or base / "security-identity-anchors.json").read_bytes()
    identity = json.loads(identity_raw)
    public_sec(identity)
    for entry in identity["identity_anchors"] + identity["outstanding_shares"]:
        public_sec(entry)
    files["identity.json"] = identity_raw
    files["certification-summary.json"] = summary_raw
    owner_path = owner_projection or base / "owner-market-exact"
    owner_raw = (owner_path / "manifest.json").read_bytes()
    owner = json.loads(owner_raw)
    if owner.get("rights") != RIGHTS or owner.get("schema_version") != "owner-market-release-v1":
        raise ValueError("OWNER_MARKET_POLICY_REQUIRED")
    if (
        owner.get("provider") != "ALPACA"
        or owner.get("feed") != "SIP"
        or owner.get("source_contracts", {}).get("no_model_rows") is not True
        or owner.get("source_contracts", {}).get("no_licensed_rows") is not True
    ):
        raise ValueError("OWNER_MANIFEST_POLICY_REQUIRED")
    for reference in owner["sources"]:
        if (
            reference.get("bucket") != BUCKET
            or not reference.get("key", "").startswith("normalized/alpaca/source-raw-daily-v1/")
            or ".." in reference["key"]
        ):
            raise ValueError("OWNER_MANIFEST_SOURCE_PATH_BLOCKED")
    owner_identity = {k: v for k, v in owner.items() if k != "release_id"}
    if sha(encode(owner_identity)) != owner["release_id"]:
        raise ValueError("OWNER_MARKET_IDENTITY_MISMATCH")
    market_data = {}
    for symbol, reference in owner["symbols"].items():
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol)
            or reference["local_file"] != symbol + ".json"
        ):
            raise ValueError("OWNER_SYMBOL_REFERENCE_INVALID")
        raw = local_verified(owner_path / reference["local_file"], reference)
        data = json.loads(raw)
        validate_market(data, symbol)
        market_data[symbol] = data
        files["market/" + symbol + ".json"] = raw
    spec = importlib.util.spec_from_file_location(
        "publication_security_identity", repo / "deploy/cloudflare-query/src/security_identity.py"
    )
    identity_runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity_runtime)
    joined_points, point_gaps = point_join_gate(
        identity, market_data, companion_data, identity_runtime.resolve_point, minimum_point_joins
    )
    implementations = {}
    for name in ("financial_certification.py", "security_identity.py", "valuation_engine.py"):
        raw = (repo / "deploy/cloudflare-query/src" / name).read_bytes()
        files["implementation/" + name] = raw
        implementations[name] = dict(sha256=sha(raw), bytes=len(raw))
    captured = (certification / "implementation/financial_certification.py").read_bytes()
    if sha(captured) != summary["certifier_implementation_sha256"]:
        raise ValueError("EXECUTED_CERTIFIER_HASH_MISMATCH")
    files["implementation/executed_financial_certification.py"] = captured
    for name in (
        "build_sec_metric_certification.py",
        "build_sec_metric_certification_historical.py",
        "financial_certification_hardened.py",
    ):
        raw = (certification / "implementation" / name).read_bytes()
        files["implementation/" + name] = raw
    expanded_builder = (
        certification / "implementation/build_sec_metric_certification_expanded_historical.py"
    )
    if expanded_builder.exists():
        files["implementation/build_sec_metric_certification_expanded_historical.py"] = (
            expanded_builder.read_bytes()
        )
    if (
        sha(files["implementation/build_sec_metric_certification.py"])
        != summary["executed_builder_implementation_sha256"]
    ):
        raise ValueError("EXECUTED_BUILDER_HASH_MISMATCH")
    if (
        sha(files["implementation/financial_certification_hardened.py"])
        != summary["current_certifier_implementation_sha256"]
    ):
        raise ValueError("HARDENED_CERTIFIER_HASH_MISMATCH")
    identity_seed = dict(
        schema_version="unified-research-release-v1",
        financial_release_id=FINANCIAL_RELEASE,
        financial_manifest_ref=financial["root"],
        owner_projection_release_id=owner["release_id"],
        original_owner_manifest_sha256=sha(owner_raw),
        inputs={k: dict(sha256=sha(v), bytes=len(v)) for k, v in sorted(files.items())},
        source_policy=dict(
            public_sec=True,
            owner_market=RIGHTS,
            human_research_only=True,
            source_prices_not_llm=True,
            licensed_wrds_allowed=False,
        ),
        implementations=implementations,
        demonstrated_point_joins=joined_points,
        point_join_gaps=point_gaps,
    )
    release_id = sha(encode(identity_seed))
    prefix = PREFIX + release_id + "/"
    # Finish every local policy/hash gate before the first cloud mutation.
    if sum(map(len, files.values())) > 11 * 1024**2 or len(files) + 2 > 100:
        raise ValueError("PUBLICATION_PREFLIGHT_BUDGET")
    publisher = Publisher(s3)
    references = {name: publisher.write(prefix + name, raw) for name, raw in sorted(files.items())}
    market = {k: v for k, v in owner.items() if k not in ("symbols", "release_id")}
    market["symbols"] = {
        symbol: dict(
            references["market/" + symbol + ".json"],
            **{k: v for k, v in entry.items() if k not in ("local_file", "sha256", "bytes")},
        )
        for symbol, entry in owner["symbols"].items()
    }
    market["projection_release_id"] = owner["release_id"]
    market["original_local_manifest_sha256"] = sha(owner_raw)
    market["release_id"] = sha(encode(market))
    market_ref = publisher.write(prefix + "market/manifest.json", encode(market))
    root = {k: v for k, v in identity_seed.items() if k != "inputs"}
    root.update(
        release_id=release_id,
        certifications={
            cik: dict(references[entry["name"]], **{k: v for k, v in entry.items() if k != "name"})
            for cik, entry in companions.items()
        },
        identity=references["identity.json"],
        market=market_ref,
        certification_summary=references["certification-summary.json"],
        implementation_refs={
            name: references["implementation/" + name] for name in implementations
        },
        executed_certifier=references["implementation/executed_financial_certification.py"],
        captured_implementation_refs={
            name: reference
            for name, reference in references.items()
            if name.startswith("implementation/")
        },
        content_identity=identity_seed,
    )
    root_ref = publisher.write(prefix + "manifest.json", encode(root))
    receipt = dict(
        status="PASS",
        release_id=release_id,
        root=root_ref,
        financial_release_id=FINANCIAL_RELEASE,
        certifications=len(companions),
        demonstrated_point_joins=len(joined_points),
        minimum_point_joins=minimum_point_joins,
        market_symbols=len(owner["symbols"]),
        cloud_create_calls=publisher.creates,
        cloud_bytes_written=publisher.bytes,
        readback_hash_checks=publisher.readbacks,
        existing_objects_verified=publisher.existing_objects,
        logical_publication_bytes=publisher.reserved_bytes,
        current_pointer_writes=0,
        configuration_writes=0,
        provider_requests=0,
        licensed_sources=0,
        source_price_rows_printed=0,
    )
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    for name, value in [("publication.json", receipt), ("manifest.json", root)]:
        (output / name).write_bytes(encode(value))
        (output / name).chmod(0o600)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--financial-publication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owner-projection", type=Path)
    parser.add_argument("--certification-dir", type=Path)
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--expected-companies", type=int, default=20)
    parser.add_argument("--minimum-point-joins", type=int, default=0)
    args = parser.parse_args()
    from reconcile_financial_inventory import client

    try:
        print(
            json.dumps(
                publish(
                    args.base,
                    args.financial_publication,
                    Path(__file__).parents[1],
                    args.output,
                    client(),
                    owner_projection=args.owner_projection,
                    certification_dir=args.certification_dir,
                    expected_companies=args.expected_companies,
                    minimum_point_joins=args.minimum_point_joins,
                    identity_file=args.identity_file,
                )
            )
        )
    except Exception as exc:
        print(json.dumps(dict(status="FAIL", error_type=type(exc).__name__)))
        raise SystemExit(1) from None
