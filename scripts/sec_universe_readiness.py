"""Issuer-universe valuation readiness over the pinned immutable SEC archive.

This is the scaling step from three verified filers to the whole archive. It downloads nothing
from the SEC and recomputes no normalized financial history: the recorded engine checkpoints
already carry per-metric observation counts, statement families and mapping policy for every
archived issuer, and the pinned Company Facts archive in R2 is the immutable source those
checkpoints were produced from. Members are read straight out of that archive with ranged
requests over a bounded block cache -- no full archive copy is written to disk.

What this adds is the *capability* question the coverage counts cannot answer. Financial
history is not valuation readiness: an issuer also needs reconcilable debt and lease
components, verified preferred and noncontrolling claims, a dated outstanding-share basis and a
method whose inputs the engine actually implements. The canonical claims contract
(`equity_claims.build_claims`, the same extractor the live three-filer pipeline uses) is run
over each issuer's raw member at the archive cutoff, and its evidence states -- REPORTED,
DERIVED_VERIFIED, REVIEWED_ABSENT, ASSUMED, CONFLICTING, UNAVAILABLE -- are preserved per
claim. A missing claim is never converted to zero.

Eligibility is reported per method, and only for methods the engine implements today (FCFF
operating DCF, FCFE, APV, bank excess return, residual income, DDM, comparables). A method that
is theoretically appropriate but unimplemented is named, never counted.

    python3 scripts/sec_universe_readiness.py --verify-archive
    python3 scripts/sec_universe_readiness.py --cohort cohort.json --maximum-seconds 900
    python3 scripts/sec_universe_readiness.py --total 20396 --workers 6
    python3 scripts/sec_universe_readiness.py --publish
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import resource
import sys
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/cloudflare-query/src"))
sys.path.insert(0, str(ROOT / "scripts"))

from equity_claims import CERTIFIED_STATES, build_claims  # noqa: E402
from equity_claims import VERSION as CLAIMS_VERSION  # noqa: E402

ENGINE = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-engine-scale")
SPRINT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-universe-sprint")
OUT = Path("/Users/scottthomasswitzer/Documents/Secret/artifacts/sec-readiness")
BUCKET = "financial-system-datasets"
PREFIX = "gold/sec-readiness/releases/"
SCHEMA_VERSION = "zion-issuer-readiness-v1"
BLOCK = 16 * 1048576
BRIDGE_CLAIMS = ("debt", "finance_leases", "preferred_equity", "noncontrolling_interests",
                 "cash", "nonoperating_assets")
METHODS = ("operating_dcf", "fcfe", "apv", "bank_excess_return", "residual_income", "ddm",
           "comparables")
# Applicability statements, not evidence gaps. An industrial filer is not "missing" the bank
# lane and a bank is not "missing" an enterprise DCF, so these must never appear in a ranking of
# missing source concepts or of work that would unlock issuers.
NOT_APPLICABLE = ("NOT_A_FINANCIAL_STATEMENT_FAMILY_FILER",
                  "INDUSTRIAL_DCF_NOT_APPLICABLE_TO_FINANCIAL_FILER")

# Concepts the claims contract does not map but a method decision needs. Their presence is a
# capability flag only; no value is derived from them here.
DIVIDEND_CONCEPTS = (
    "PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
    "PaymentsOfDividendsMinorityInterest", "CommonStockDividendsPerShareDeclared",
    "DividendsCommonStockCash", "PaymentsOfDividendsPreferredStockAndPreferenceStock",
)
DILUTION_CONCEPTS = (
    "ShareBasedCompensation", "AllocatedShareBasedCompensationExpense",
    "EmployeeServiceShareBasedCompensationNonvestedAwardsTotalCompensationCostNotYetRecognized",
    "StockIssuedDuringPeriodSharesStockOptionsExercised", "ConvertibleDebtNoncurrent",
    "ConvertibleNotesPayable",
    "AntidilutiveSecuritiesExcludedFromComputationOfEarningsPerShareAmount",
    "IncrementalCommonSharesAttributableToShareBasedPaymentArrangements",
)
DILUTION_TAGS = ("ShareBasedCompensation", "StockOption", "RestrictedStock", "Convertible",
                 "Warrant", "EmployeeStock")
GOODWILL_CONCEPTS = ("Goodwill", "IntangibleAssetsNetExcludingGoodwill")
DIMENSION_KEYS = ("segment", "class", "member", "dimension", "axis", "geographical", "range")
SHARE_CONCEPTS = ("CommonStockSharesOutstanding", "CommonStockSharesIssued",
                  "EntityCommonStockSharesOutstanding")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=str).encode()


def save_gzip(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(gzip.compress(encoded(value), mtime=0))
    os.replace(tmp, path)


def manifest():
    base = json.loads((ENGINE / "engine-manifest.json").read_text())
    entries = json.loads((ENGINE / "full-member-index.json").read_text())
    return base, entries


def materialization_release(base):
    """Stable identity of the inputs, so a resumed or partial run keeps one directory."""
    return digest(encoded({
        "schema_version": SCHEMA_VERSION,
        "engine_release_id": base["release_id"],
        "registry_sha256": base["registry_sha256"],
        "claims_contract_version": CLAIMS_VERSION,
        "archive_cutoff": base["date_to"],
        "archive_declared_sha256": base["source"]["declared_sha256"],
    }))


def publication_identity(materialization, records):
    """Immutable snapshot identity: the inputs plus the exact classified evidence set."""
    return digest(encoded({
        "materialization_release": materialization,
        "records": {cik: digest(encoded(record)) for cik, record in sorted(records.items())},
    }))


def recorded():
    """The engine checkpoints: the financial-history evidence already reconciled per issuer."""
    records = {}
    for path in (SPRINT / "checkpoints").glob("*.json.gz"):
        with gzip.open(path) as handle:
            records[path.name.split(".")[0]] = json.load(handle)
    return records


# --------------------------------------------------------------------------------------------------
# Ranged archive access
# --------------------------------------------------------------------------------------------------
class RangeReader(io.RawIOBase):
    """A seekable view of one byte range of an R2 object, over a bounded block cache.

    Members are consumed in ascending offset order, so a block is normally fetched once. The
    cache exists for the central directory at the end of the object and for a member that
    crosses a block boundary, not to allow random access at scale.
    """

    def __init__(self, source, client, *, start=0, end=None, block=BLOCK, cached=6,
                 hash_blocks=False):
        self.source, self.s, self.block, self.cached = source, client, block, cached
        self.start = start
        self.end = source["bytes"] if end is None else end
        self.offset = 0
        self.blocks = OrderedDict()
        self.requests = 0
        self.bytes_read = 0
        self.hash_blocks = hash_blocks
        self.hasher = hashlib.sha256()
        self.next_hash_block = 0
        self.hash_gaps = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.offset = offset
        elif whence == io.SEEK_CUR:
            self.offset += offset
        elif whence == io.SEEK_END:
            self.offset = self.size() + offset
        return self.offset

    def tell(self):
        return self.offset

    def size(self):
        return self.end - self.start

    def _block(self, index):
        raw = self.blocks.get(index)
        if raw is None:
            low = index * self.block
            high = min(low + self.block, self.end - self.start)
            response = self.s.get_object(
                Bucket=self.source["bucket"], Key=self.source["key"],
                Range=f"bytes={self.start + low}-{self.start + high - 1}",
                IfMatch=self.source["etag"])
            try:
                raw = response["Body"].read(high - low)
            finally:
                response["Body"].close()
            if len(raw) != high - low:
                raise ValueError("ARCHIVE_BLOCK_LENGTH")
            self.bytes_read += len(raw)
            self.requests += 1
            if self.hash_blocks:
                if index == self.next_hash_block:
                    self.hasher.update(raw)
                    self.next_hash_block += 1
                else:
                    self.hash_gaps += 1
            self.blocks[index] = raw
            if len(self.blocks) > self.cached:
                self.blocks.popitem(last=False)
        self.blocks.move_to_end(index)
        return raw

    def readinto(self, buffer):
        size = len(buffer)
        if self.offset >= self.size():
            return 0
        size = min(size, self.size() - self.offset)
        first, last = self.offset // self.block, (self.offset + size - 1) // self.block
        parts = []
        for index in range(first, last + 1):
            block = self._block(index)
            low, high = index * self.block, (index + 1) * self.block
            parts.append(block[max(self.offset - low, 0):min(self.offset + size - low, high - low)])
        chunk = b"".join(parts)
        buffer[:len(chunk)] = chunk
        self.offset += len(chunk)
        return len(chunk)

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.size() - self.offset
        buffer = bytearray(size)
        got = self.readinto(buffer)
        return bytes(buffer[:got])


def archive_status(source, client):
    """One sequential pass: the raw bytes must hash to the digest the manifest declares.

    The engine run compared only the object ETag, so the declared archive digest was recorded
    but never independently recomputed. This recomputes it.
    """
    reader = RangeReader(source, client, hash_blocks=True)
    started = time.perf_counter()
    while True:
        if not reader.read(BLOCK):
            break
    observed = reader.hasher.hexdigest()
    return {
        "declared_sha256": source["declared_sha256"],
        "observed_sha256": observed,
        "declared_by_manifest": observed == source["declared_sha256"],
        "if_match_etag": source["etag"],
        "bytes": source["bytes"],
        "blocks": reader.requests,
        "blocks_hashed_out_of_order": reader.hash_gaps,
        "hash_contiguous_from_start": (
            reader.next_hash_block == (source["bytes"] + BLOCK - 1) // BLOCK),
        "seconds": round(time.perf_counter() - started, 2),
    }


def member_status(source, client, entries):
    """The archive's own central directory must agree with the pinned member index."""
    reader = RangeReader(source, client, hash_blocks=False)
    with ZipFile(reader) as zf:
        listed = {info.filename: info for info in zf.infolist()}
    missing = sorted(set(entries) - set(listed))
    extra = sorted(set(listed) - set(entries))
    mismatch = []
    for name, entry in entries.items():
        info = listed.get(name)
        if info is None:
            continue
        if (info.file_size != entry["uncompressed_bytes"]
                or info.compress_size != entry["compressed_bytes"] or info.CRC != entry["crc"]
                or info.header_offset != entry["offset"]
                or info.compress_type != entry["compression"]):
            mismatch.append(name)
    return {
        "members_pinned": len(entries), "members_listed": len(listed),
        "members_missing_from_archive": missing[:20], "unpinned_members_in_archive": extra[:20],
        "member_identity_or_size_mismatches": mismatch[:20],
        "status": "PASS" if not (missing or extra or mismatch) else "FAIL",
    }


# --------------------------------------------------------------------------------------------------
# Per-issuer readiness
# --------------------------------------------------------------------------------------------------
def presence(facts, concepts, taxonomy="us-gaap"):
    definitions = facts.get(taxonomy, {})
    found = {}
    for concept in concepts:
        definition = definitions.get(concept)
        if definition and definition.get("units"):
            found[concept] = sum(len(rows) for rows in definition["units"].values())
    return found


def tag_presence(facts, tags):
    """Concepts whose names contain a tag, for capabilities without a fixed concept list."""
    hits = {}
    for concept, definition in facts.get("us-gaap", {}).items():
        if any(tag in concept for tag in tags) and definition.get("units"):
            hits[concept] = sum(len(rows) for rows in definition["units"].values())
    return hits


def dimensional(row):
    """A dimensional fact carries a member beyond the standard period/filing keys."""
    if not isinstance(row, dict):
        return False
    return any(any(token in key.lower() for token in DIMENSION_KEYS) for key in row)


def class_axis_presence(facts):
    """Whether the filer reports share concepts per class.

    Company Facts does not expose per-class share counts in a usable form, so this records the
    *existence* of class-scoped share facts -- a genuine multiple-class signal -- and never a
    per-class number.
    """
    hits = {}
    for taxonomy in ("us-gaap", "dei"):
        for concept, definition in facts.get(taxonomy, {}).items():
            if concept not in SHARE_CONCEPTS:
                continue
            for rows in definition.get("units", {}).values():
                count = sum(1 for row in rows if dimensional(row))
                if count:
                    hits[concept] = hits.get(concept, 0) + count
    return hits


def claim_summary(contract):
    claims = {}
    for name, value in sorted(contract["claims"].items()):
        claims[name] = {
            "evidence_state": value["evidence_state"],
            "value_present": value["value_decimal"] is not None,
            "permitted_for_valuation": value.get("permitted_for_valuation"),
            "verified_absent": bool(value.get("verified_absent")),
            "basis": value.get("basis"),
            "sources": len(value.get("sources") or []),
        }
    dated = contract["claims"]["common_shares"].get("dated_observations") or []
    shares = {
        "evidence_state": claims["common_shares"]["evidence_state"],
        "basis": claims["common_shares"]["basis"],
        "dated_observations": len(dated),
        "latest_observation_period_end": (sorted(d.get("period_end", "") for d in dated)[-1]
                                          if dated else None),
        "class_detail": (contract["claims"]["common_shares"].get("classes") or {}).get(
            "evidence_state"),
        "not_a_weighted_average": bool(
            contract["claims"]["common_shares"].get("not_a_weighted_average")),
    }
    return claims, shares, contract["aggregate_claims"]


def financial_history(recorded_record):
    ref = (recorded_record or {}).get("ref") or {}
    coverage = ref.get("coverage") or {}
    annual = coverage.get("annual") or {}
    metrics = {metric: int(values.get("observations") or 0) for metric, values in annual.items()}
    available = sorted(metric for metric, count in metrics.items() if count)
    screen = (recorded_record or {}).get("screen") or {}
    latest = None
    for row in (screen.get("metrics") or {}).values():
        if row and row.get("period_end") and (latest is None or row["period_end"] > latest):
            latest = row["period_end"]
    if recorded_record is None:
        status = "NOT_ATTEMPTED"
    elif recorded_record.get("status") == "USABLE":
        status = "USABLE_HISTORY"
    else:
        status = "QUARANTINED"
    return {
        "status": status,
        "quarantine_reason": (recorded_record or {}).get("reason"),
        "annual_metrics_available": available,
        "annual_observations": metrics,
        "quarterly_metrics_available": sorted(
            metric for metric, values in (coverage.get("quarterly") or {}).items()
            if values.get("observations")),
        "ttm_metrics_available": sorted(
            metric for metric, values in (coverage.get("ttm") or {}).items()
            if values.get("observations")),
        "latest_annual_period_end": latest,
        "normalized_revisions": ref.get("normalized_revisions"),
        "statement_family": (ref.get("policy") or {}).get("family"),
        "debt_components_observed": (ref.get("policy") or {}).get("debt_components") or [],
        "quarantined_metrics": (ref.get("policy") or {}).get("quarantined_metrics") or {},
        "source_records": (ref.get("source_inventory") or {}).get("counts") or {},
        "source_eligibility": (recorded_record or {}).get("source_eligibility"),
    }


def method_eligibility(history, claims, shares, capabilities):
    """Eligibility per implemented method, each refusal naming the exact missing input.

    Rules are stated against what the engine actually consumes, so a method is never reported
    usable because it would be theoretically appropriate.
    """
    available = set(history["annual_metrics_available"])
    certified = {name: claims[name]["evidence_state"] in CERTIFIED_STATES for name in claims}
    shares_ok = shares["evidence_state"] in CERTIFIED_STATES
    financial = history["statement_family"] == "financial_statement_evidence"
    operating = {"revenue", "operating_income"} <= available
    cash_flow = {"operating_cash_flow", "capital_expenditures"} <= available
    book = bool(capabilities["equity_totals_reported"])
    preferred_clear = certified["preferred_equity"] and certified["noncontrolling_interests"]
    methods = {}

    def rule(name, required, missing, note):
        if financial and name in ("operating_dcf", "fcfe", "apv"):
            # The industrial lanes do not apply to a financial-statement filer, so their
            # evidence gaps are the same applicability statement rather than separate missing
            # concepts. Reporting them as evidence would make every bank look like it is
            # missing cash-flow mappings it does not need for its own lane.
            missing = ["INDUSTRIAL_DCF_NOT_APPLICABLE_TO_FINANCIAL_FILER"]
        methods[name] = {"eligible": not missing, "missing": sorted(set(missing)),
                         "required": required, "note": note}

    missing = []
    if not operating:
        missing.append("MISSING_OPERATING_INCOME_OR_REVENUE")
    if not cash_flow:
        missing.append("MISSING_CASH_FLOW_OR_CAPEX")
    missing += ["MISSING_BRIDGE_CLAIM_" + name.upper() for name in BRIDGE_CLAIMS
                if not certified[name]]
    if not shares_ok:
        missing.append("MISSING_DATED_OUTSTANDING_SHARES")
    if financial:
        missing.append("INDUSTRIAL_DCF_NOT_APPLICABLE_TO_FINANCIAL_FILER")
    rule("operating_dcf", ["revenue", "operating_income", "operating_cash_flow",
                           "capital_expenditures", "complete_equity_bridge", "shares"], missing,
         "FCFF enterprise DCF as the engine implements it, including the equity bridge.")

    missing = []
    if not operating:
        missing.append("MISSING_OPERATING_INCOME_OR_REVENUE")
    if not cash_flow:
        missing.append("MISSING_CASH_FLOW_OR_CAPEX")
    if "net_income" not in available:
        missing.append("MISSING_NET_INCOME")
    if not shares_ok:
        missing.append("MISSING_DATED_OUTSTANDING_SHARES")
    rule("fcfe", ["revenue", "operating_income", "operating_cash_flow", "capital_expenditures",
                  "net_income", "shares"], missing,
         "Equity cash flow discounted at the cost of equity; no enterprise-to-equity bridge is "
         "needed, so this lane survives an incomplete bridge.")

    missing = []
    if not operating or not cash_flow:
        missing.append("MISSING_UNLEVERED_FREE_CASH_FLOW_INPUTS")
    if not certified["debt"]:
        missing.append("MISSING_BRIDGE_CLAIM_DEBT")
    if not shares_ok:
        missing.append("MISSING_DATED_OUTSTANDING_SHARES")
    rule("apv", ["unlevered_fcf_inputs", "debt_claim", "shares"], missing,
         "Unlevered value plus explicitly-discounted tax shields; needs a debt schedule.")

    missing = []
    if not financial:
        missing.append("NOT_A_FINANCIAL_STATEMENT_FAMILY_FILER")
    if "net_income" not in available:
        missing.append("MISSING_NET_INCOME")
    if not book:
        missing.append("MISSING_BOOK_EQUITY_TOTAL")
    if not certified["preferred_equity"]:
        missing.append("MISSING_BRIDGE_CLAIM_PREFERRED_EQUITY")
    if not shares_ok:
        missing.append("MISSING_DATED_OUTSTANDING_SHARES")
    rule("bank_excess_return", ["financial_family", "net_income", "book_equity",
                               "preferred_claim", "shares"], missing,
         "Bank/financial equity excess-return lane (the eqexret framework).")

    missing = []
    if "net_income" not in available:
        missing.append("MISSING_NET_INCOME")
    if not book:
        missing.append("MISSING_BOOK_EQUITY_TOTAL")
    if not preferred_clear:
        missing.append("PREFERRED_OR_NCI_CLAIM_NOT_RESOLVED")
    if not shares_ok:
        missing.append("MISSING_DATED_OUTSTANDING_SHARES")
    rule("residual_income", ["net_income", "book_equity", "preferred_and_nci_clear", "shares"],
         missing, "Residual income blocks preferred and noncontrolling claims by design.")

    missing = [] if capabilities["dividends_reported"] else ["MISSING_DIVIDEND_EVIDENCE"]
    if not shares_ok:
        missing.append("MISSING_DATED_OUTSTANDING_SHARES")
    rule("ddm", ["dividends", "shares"], missing,
         "Dividend discount model; a payer is identified by dividend concepts being filed.")

    missing = []
    if not (available & {"revenue", "net_income"}):
        missing.append("MISSING_REVENUE_AND_NET_INCOME")
    if not shares_ok:
        missing.append("MISSING_DATED_OUTSTANDING_SHARES")
    rule("comparables", ["revenue_or_net_income", "shares"], missing,
         "Peer multiples are analysis, not an intrinsic value; it does not price a company.")

    eligible = sorted(name for name, value in methods.items() if value["eligible"])
    return {
        "methods": methods,
        "eligible_methods": eligible,
        "intrinsic_value_methods": [name for name in eligible if name != "comparables"],
        "requires_specialized_method": financial,
        "notes": [
            "Only methods the engine implements are evaluated; a suitable but unimplemented "
            "method is not reported as usable.",
            "Eligibility is a company-side input test. Every method still needs dated "
            "discount-rate assumptions and forecast drivers, which are assumptions rather than "
            "filings.",
        ],
        "unimplemented_but_appropriate": (
            ["specialized_financial_company_models_beyond_excess_return"] if financial else []),
    }


def readiness_for(cik, label, raw, recorded_record, base, member_sha):
    document = json.loads(raw)
    if str(document.get("cik", "")).zfill(10) != cik:
        return {"cik": cik, "label": label, "status": "SOURCE_IDENTITY_MISMATCH",
                "member_sha256": member_sha}
    facts = document.get("facts") or {}
    contract = build_claims(facts, date.fromisoformat(base["date_to"]), cik=cik)
    claims, shares, aggregate = claim_summary(contract)
    history = financial_history(recorded_record)
    dividends = sorted(presence(facts, DIVIDEND_CONCEPTS))
    capabilities = {
        "dividends_reported": bool(dividends),
        "dividend_concepts": dividends,
        "dilution_concepts": sorted(presence(facts, DILUTION_CONCEPTS)),
        "dilution_tags": sorted(tag_presence(facts, DILUTION_TAGS)),
        "goodwill_reported": bool(presence(facts, GOODWILL_CONCEPTS)),
        "equity_totals_reported": bool(contract["reconciliation"]["reported_equity_totals"]),
        "multiple_share_classes": bool(class_axis_presence(facts)),
        "taxonomies": sorted(facts),
    }
    methods = method_eligibility(history, claims, shares, capabilities)
    missing = []
    for value in methods["methods"].values():
        if not value["eligible"]:
            missing += [code for code in value["missing"] if code not in NOT_APPLICABLE]
    return {
        "cik": cik,
        "label": label,
        "schema_version": SCHEMA_VERSION,
        "claims_contract_version": CLAIMS_VERSION,
        "status": "CLASSIFIED",
        "source": {
            "release_id": base["release_id"],
            "registry_sha256": base["registry_sha256"],
            "archive_snapshot_date": base["source"]["snapshot_date"],
            "archive_cutoff": base["date_to"],
            "availability_cutoff": base["maximum_as_of"],
            "archive_declared_sha256": base["source"]["declared_sha256"],
            "member_sha256": member_sha,
            "rights": base["rights"],
        },
        "financial_history": history,
        "equity_claims": {"claims": claims, "aggregate": aggregate, "shares": shares,
                          "unresolved": contract["unresolved"]},
        "capabilities": capabilities,
        "valuation": methods,
        "missing_inputs": sorted(set(missing)),
        "readiness": {
            "financial_history_available": history["status"] == "USABLE_HISTORY",
            "equity_claims_mapped": all(claims[name]["evidence_state"] != "UNAVAILABLE"
                                        for name in BRIDGE_CLAIMS),
            "complete_equity_bridge": aggregate["status"] == "AVAILABLE",
            "dated_share_basis_verified": shares["evidence_state"] in CERTIFIED_STATES,
            "any_implemented_method_eligible": bool(methods["intrinsic_value_methods"]),
            "operating_dcf_eligible": methods["methods"]["operating_dcf"]["eligible"],
            "specialized_method_required": methods["requires_specialized_method"],
        },
    }


def classify_range(job):
    """One worker: one contiguous archive range, its members, one append-only work file."""
    index, ciks, entries, source, history, base, work_path = job
    from reconcile_financial_inventory import client
    s = client()
    started, processed, failures, bytes_read = time.perf_counter(), 0, 0, 0
    handle = work_path.open("a")
    reader = RangeReader(source, s, hash_blocks=False)
    try:
        with ZipFile(reader) as zf:
            for cik in ciks:
                try:
                    raw = zf.read(entries[cik]["name"])
                except Exception as error:  # noqa: BLE001 - a bad member is data, not a crash
                    record = {"cik": cik, "status": "ARCHIVE_READ_FAILURE",
                              "error_type": type(error).__name__, "detail": str(error)[:140]}
                else:
                    label = (history.get(cik) or {}).get("ref", {}).get("label")
                    record = readiness_for(cik, label, raw, history.get(cik), base, digest(raw))
                    record["source_bytes"] = len(raw)
                    bytes_read += len(raw)
                if record.get("status") != "CLASSIFIED":
                    failures += 1
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False) + "\n")
                processed += 1
                if processed % 50 == 0:
                    handle.flush()
    finally:
        handle.close()
        reader.close()
    return {"range": index, "ciks": len(ciks), "processed": processed, "failures": failures,
            "source_bytes": bytes_read, "seconds": round(time.perf_counter() - started, 2),
            "r2_range_requests": reader.requests, "work": work_path.name}


def plan_ranges(selected, entries, workers):
    """Contiguous compressed-byte ranges, so each worker streams its slice once."""
    ordered = sorted(selected, key=lambda cik: entries[cik]["offset"])
    # Balanced on decompressed bytes, which is what the worker actually spends CPU on, while the
    # offset order above keeps every worker reading its archive slice forward.
    total = sum(entries[cik]["uncompressed_bytes"] for cik in ordered)
    per = max(total // workers, 1)
    ranges, current, size = [], [], 0
    for cik in ordered:
        current.append(cik)
        size += entries[cik]["uncompressed_bytes"]
        if size >= per and len(ranges) < workers - 1:
            ranges.append(current)
            current, size = [], 0
    if current:
        ranges.append(current)
    return ranges


def work_records(work_dir):
    records = {}
    for path in sorted(work_dir.glob("range-*.jsonl")):
        with path.open() as handle:
            for line in handle:
                if not line.endswith("\n"):
                    continue  # a torn final line is discarded, and the issuer is retried
                record = json.loads(line)
                records[record["cik"]] = record
    return records


# --------------------------------------------------------------------------------------------------
# Summary, ranking, publication
# --------------------------------------------------------------------------------------------------
def top_missing(records):
    impact = {}
    for record in records.values():
        if record.get("status") != "CLASSIFIED":
            continue
        for code in record["missing_inputs"]:
            impact[code] = impact.get(code, 0) + 1
    ranked = sorted(impact.items(), key=lambda item: (-item[1], item[0]))
    return [{"code": code, "issuers_affected": count} for code, count in ranked[:20]]


def rank_work(records):
    """Which single improvement unlocks the most additional issuers.

    An issuer counts toward a scenario only when that method refuses it for *exactly* one
    reason, so resolving that reason is sufficient for eligibility. That makes this a ranking of
    work rather than a ranking of symptoms.
    """
    unlock, bridge_blockers = {}, {}
    for record in records.values():
        if record.get("status") != "CLASSIFIED":
            continue
        for method, value in record["valuation"]["methods"].items():
            if value["eligible"]:
                continue
            codes = set(value["missing"])
            if len(codes) == 1:
                key = method + ":" + codes.pop()
                unlock[key] = unlock.get(key, 0) + 1
            if method == "operating_dcf":
                for code in codes:
                    if code.startswith("MISSING_BRIDGE_CLAIM_"):
                        bridge_blockers[code] = bridge_blockers.get(code, 0) + 1
    ranked = sorted(unlock.items(), key=lambda item: (-item[1], item[0]))
    return {
        "single_input_unlocks": [{"method_and_input": key, "issuers_unlocked": count}
                                 for key, count in ranked[:20]],
        "unresolved_equity_bridge_claims_blocking_operating_dcf": dict(
            sorted(bridge_blockers.items(), key=lambda item: -item[1])),
    }


def summary(records, base, entries, elapsed, throughput):
    counts, per_method = {}, {}
    for name in METHODS:
        per_method[name] = {"eligible": 0, "by_missing_input": {}, "share_of_inspected": 0.0}
    classified = 0
    for record in records.values():
        if record.get("status") != "CLASSIFIED":
            counts["not_classified_" + str(record.get("status"))] = counts.get(
                "not_classified_" + str(record.get("status")), 0) + 1
            continue
        classified += 1
        for key, value in record["readiness"].items():
            counts[key] = counts.get(key, 0) + int(bool(value))
        for name in METHODS:
            if record["valuation"]["methods"][name]["eligible"]:
                per_method[name]["eligible"] += 1
            else:
                for code in record["valuation"]["methods"][name]["missing"]:
                    entry = per_method[name]["by_missing_input"]
                    entry[code] = entry.get(code, 0) + 1
    for name in METHODS:
        per_method[name]["share_of_inspected"] = (
            round(100 * per_method[name]["eligible"] / classified, 2) if classified else 0.0)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_release_id": base["release_id"],
        "registry_sha256": base["registry_sha256"],
        "archive_cutoff": base["date_to"],
        "archive_snapshot_date": base["source"]["snapshot_date"],
        "denominators": {
            "archived_issuers_discovered": len(entries),
            "issuers_inspected": len(records),
            "issuers_classified": classified,
            "issuers_inspected_percent_of_archive": round(100 * len(records) / len(entries), 2),
            "engine_usable_history_denominator": sum(
                1 for r in records.values()
                if (r.get("financial_history") or {}).get("status") == "USABLE_HISTORY"),
        },
        "readiness_counts": counts,
        "readiness_percent_of_classified": {
            key: round(100 * value / classified, 2) if classified else 0.0
            for key, value in counts.items() if not key.startswith("not_classified")},
        "method_eligibility": per_method,
        "any_implemented_intrinsic_method": sum(
            1 for r in records.values()
            if (r.get("readiness") or {}).get("any_implemented_method_eligible")),
        "operating_dcf_eligible": sum(
            1 for r in records.values()
            if (r.get("readiness") or {}).get("operating_dcf_eligible")),
        "fcfe_eligible_without_complete_bridge": sum(
            1 for r in records.values() if r.get("status") == "CLASSIFIED"
            and r["valuation"]["methods"]["fcfe"]["eligible"]
            and not r["readiness"]["complete_equity_bridge"]),
        "top_missing_inputs": top_missing(records),
        "work_ranking": rank_work(records),
        "throughput": throughput | {"seconds": round(elapsed, 1)},
        "qualification": (
            "COMPANY_SIDE_INPUT_ELIGIBILITY;ASSUMPTIONS_AND_DISCOUNT_RATES_EXCLUDED;"
            "REVIEWED_ABSENT_IS_A_VERIFIED_ZERO;UNMAPPED_RAW_CONCEPTS_REMAIN_IN_ARCHIVE"),
    }


def publish(records, base, out, summary_value, materialization, release):
    from reconcile_financial_inventory import client
    s = client()
    prefix = PREFIX + release + "/"
    artifacts = []
    for path in sorted(out.glob("part-*.jsonl.gz")) + [out / "summary.json", out / "manifest.json"]:
        raw = path.read_bytes()
        key = prefix + path.name
        s.put_object(Bucket=BUCKET, Key=key, Body=raw, IfNoneMatch="*")
        head = s.head_object(Bucket=BUCKET, Key=key)
        uploaded = head["ETag"].strip('"')
        if uploaded != hashlib.md5(raw).hexdigest():
            raise ValueError("PUBLICATION_READBACK_ETAG_MISMATCH:" + key)
        artifacts.append({"key": key, "bytes": len(raw), "sha256": digest(raw),
                          "readback": "HEAD_ETAG_MD5_MATCH", "etag": uploaded})
    receipt = {
        "release_id": release, "materialization_release": materialization,
        "engine_release_id": base["release_id"], "prefix": prefix, "bucket": BUCKET,
        "artifacts": artifacts, "issuers": len(records),
        "summary": summary_value,
        "published_utc": datetime.now(timezone.utc).isoformat(),
        "current_pointer_writes": 0, "licensed_sources": 0,
    }
    (out / "publication.json").write_bytes(encoded(receipt) + b"\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--total", type=int, default=20396)
    parser.add_argument("--workers", type=int, default=max(2, min(8, (os.cpu_count() or 4) - 1)))
    parser.add_argument("--maximum-seconds", type=int, default=5400)
    parser.add_argument("--cohort", type=Path, help="JSON list of CIKs instead of all members")
    parser.add_argument("--part-size", type=int, default=1000)
    parser.add_argument("--verify-archive", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="discard recorded work files")
    args = parser.parse_args()

    base, entries = manifest()
    materialization = materialization_release(base)
    out = OUT / materialization
    work_dir = out / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    if args.fresh:
        for path in work_dir.glob("range-*.jsonl"):
            path.unlink()
    from reconcile_financial_inventory import client
    s = client()

    integrity = None
    if args.verify_archive:
        integrity = {"archive": archive_status(base["source"], s),
                     "members": member_status(base["source"], s, entries)}
        (out / "archive-integrity.json").write_bytes(encoded(integrity) + b"\n")
        print(json.dumps(integrity, indent=1))

    history = recorded()
    selected = sorted(json.loads(args.cohort.read_text())) if args.cohort else sorted(entries)
    selected = [cik for cik in selected if cik in entries][:args.total]
    done = set(work_records(work_dir))
    remaining = [cik for cik in selected if cik not in done]
    print(json.dumps({"selected": len(selected), "already_classified": len(done),
                      "remaining": len(remaining), "workers": args.workers}), flush=True)
    started = time.perf_counter()
    ranges = plan_ranges(remaining, entries, args.workers)
    # Each worker receives only its own slice of the recorded checkpoints and member index; the
    # full 20,000-entry structures would otherwise be pickled once per worker.
    jobs = [(index, ciks, {cik: entries[cik] for cik in ciks}, base["source"],
             {cik: history[cik] for cik in ciks if cik in history}, base,
             work_dir / f"range-{index:03d}.jsonl") for index, ciks in enumerate(ranges)]
    receipts = []
    if remaining:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for receipt in pool.map(classify_range, jobs):
                receipts.append(receipt)
                print(json.dumps(receipt), flush=True)
    records = work_records(work_dir)
    elapsed = time.perf_counter() - started
    if args.maximum_seconds and elapsed > args.maximum_seconds:
        print(json.dumps({"note": "elapsed budget exceeded", "seconds": round(elapsed, 1)}))

    for stale in out.glob("part-*.jsonl.gz"):
        stale.unlink()
    ordered = sorted(records)
    parts = []
    for start in range(0, len(ordered), args.part_size):
        chunk = [json.dumps(records[cik], sort_keys=True, separators=(",", ":"), allow_nan=False)
                 for cik in ordered[start:start + args.part_size]]
        body = gzip.compress(("\n".join(chunk) + "\n").encode(), mtime=0)
        part = out / f"part-{start // args.part_size:04d}.jsonl.gz"
        part.write_bytes(body)
        parts.append({"part": part.name, "issuers": len(chunk), "bytes": len(body),
                      "sha256": digest(body)})

    throughput = {
        "issuers_classified_this_run": len(remaining),
        "issuers_per_second": round(len(remaining) / max(elapsed, 0.001), 2),
        "r2_range_requests": sum(r.get("r2_range_requests", 0) for r in receipts),
        "source_bytes_decompressed_this_run": sum(r.get("source_bytes", 0) for r in receipts),
        "worker_seconds": round(sum(r.get("seconds", 0) for r in receipts), 1),
        "workers": args.workers,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    summary_value = summary(records, base, entries, elapsed, throughput)
    (out / "summary.json").write_bytes(encoded(summary_value) + b"\n")
    release = publication_identity(materialization, records)
    document = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release,
        "materialization_release": materialization,
        "engine_release_id": base["release_id"],
        "archive_cutoff": base["date_to"],
        "archive": base["source"],
        "archive_integrity": integrity,
        "recorded_engine_checkpoints_used": sum(1 for cik in records if history.get(cik)),
        "parts": parts,
        "issuers": len(records),
        "claims_contract_version": CLAIMS_VERSION,
        "published": False,
    }
    (out / "manifest.json").write_bytes(encoded(document) + b"\n")
    print(json.dumps({"summary": {k: v for k, v in summary_value.items()
                                  if k not in ("top_missing_inputs", "work_ranking")}},
                     indent=1))
    if args.publish:
        receipt = publish(records, base, out, summary_value, materialization, release)
        print(json.dumps({"published": [a["key"] for a in receipt["artifacts"]],
                          "release_id": release, "issuers": receipt["issuers"]}))


if __name__ == "__main__":
    main()
