"""Company-by-field reconciliation report built from the private registry.

The full report is private evidence: it names private store paths and object
hashes, so it is written to an ignored output directory and is never published to
the public valuation site or committed to the repository. The sanitized summary
(JSON) and sanitized markdown carry the evaluated observations, states and
reason codes without any private provenance, so they are safe to commit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from zion_terminal.datasets import sec_fundamentals as sf
from zion_terminal.datasets.registry import (
    QUALITY_ABSENT,
    FinancialDatasetRegistry,
    ObjectRecord,
    ReleaseRef,
    TransportError,
)

REPORT_SCHEMA = "zion-financial-registry-report-v1"
DEFAULT_OUTPUT_DIR = Path("artifacts/registry-m1")

#: How the published serving representation maps onto the canonical contract.
#: ``None`` means the serving layer cannot satisfy the field at all.
SERVING_CONTRACT_COVERAGE: tuple[dict[str, Any], ...] = (
    {
        "field_id": "revenue",
        "serving_metric": "revenue",
        "state": "SUPPORTED",
        "basis": "Consolidated revenue.",
    },
    {
        "field_id": "operating_income_ebit",
        "serving_metric": "operating_income",
        "state": "SUPPORTED",
        "basis": "Consolidated operating income.",
    },
    {
        "field_id": "net_income_common",
        "serving_metric": "net_income",
        "state": "BASIS_DIFFERS",
        "basis": "Serving metric is consolidated net income, not income attributable to "
        "common shareholders.",
    },
    {
        "field_id": "depreciation_amortization",
        "serving_metric": None,
        "state": "ABSENT_FROM_SERVING_CATALOG",
        "basis": "No depreciation/amortization metric is published in the serving metric catalog.",
    },
    {
        "field_id": "capital_expenditure",
        "serving_metric": None,
        "state": "ABSENT_FROM_SERVING_CATALOG",
        "basis": "No capital expenditure metric is published in the serving metric catalog.",
    },
    {
        "field_id": "diluted_weighted_average_shares",
        "serving_metric": "shares_outstanding",
        "state": "BASIS_DIFFERS",
        "basis": "Serving metric is an instant common shares outstanding count, not diluted "
        "weighted-average shares.",
    },
    {
        "field_id": "cash_and_equivalents",
        "serving_metric": "cash",
        "state": "SUPPORTED",
        "basis": "Cash and cash equivalents at period end.",
    },
    {
        "field_id": "debt_current",
        "serving_metric": None,
        "state": "ABSENT_FROM_SERVING_CATALOG",
        "basis": "Serving catalog publishes no current/noncurrent debt split.",
    },
    {
        "field_id": "debt_noncurrent",
        "serving_metric": None,
        "state": "ABSENT_FROM_SERVING_CATALOG",
        "basis": "Serving catalog publishes no current/noncurrent debt split.",
    },
    {
        "field_id": "debt_short_term_borrowings",
        "serving_metric": None,
        "state": "ABSENT_FROM_SERVING_CATALOG",
        "basis": "Serving catalog publishes no short-term borrowings metric.",
    },
    {
        "field_id": "debt_total_reported",
        "serving_metric": "total_debt",
        "state": "PARTIAL",
        "basis": "Serving metric is a selected reported long-term debt obligation, not total "
        "interest-bearing debt and not total liabilities.",
    },
    {
        "field_id": sf.DERIVED_FIELD,
        "serving_metric": None,
        "state": "NOT_DERIVABLE",
        "basis": "Requires a current/noncurrent split that the serving layer does not publish.",
    },
    {
        "field_id": sf.SHORT_TERM_DERIVED_FIELD,
        "serving_metric": None,
        "state": "NOT_DERIVABLE",
        "basis": "Requires a short-term borrowings split that the serving layer does not publish.",
    },
)

#: Approved packet historical field to contract field, with comparability notes.
PACKET_HISTORICAL_MAP: tuple[dict[str, Any], ...] = (
    {"packet_field": "revenue", "field_id": "revenue", "unit": "USD", "comparable": True},
    {
        "packet_field": "ebit",
        "field_id": "operating_income_ebit",
        "unit": "USD",
        "comparable": True,
    },
    {
        "packet_field": "net_income",
        "field_id": "net_income_common",
        "unit": "USD",
        "comparable": True,
        "basis": "Packet net income sits on the same consolidated basis; comparability is "
        "confirmed against the source tag.",
    },
    {
        "packet_field": "d_and_a",
        "field_id": "depreciation_amortization",
        "unit": "USD",
        "comparable": True,
    },
    {"packet_field": "capex", "field_id": "capital_expenditure", "unit": "USD", "comparable": True},
    {
        "packet_field": "diluted_shares",
        "field_id": "diluted_weighted_average_shares",
        "unit": "shares",
        "comparable": True,
    },
)

#: Approved packet bridge entries to contract fields.
PACKET_BRIDGE_MAP: tuple[dict[str, Any], ...] = (
    {"packet_field": "cash", "field_id": "cash_and_equivalents", "unit": "USD", "comparable": True},
    {
        "packet_field": "long_term_debt",
        "field_id": "debt_noncurrent",
        "unit": "USD",
        "comparable": True,
    },
    {
        "packet_field": "short_term_debt",
        "field_id": sf.SHORT_TERM_DERIVED_FIELD,
        "unit": "USD",
        "comparable": True,
        "basis": "Packet short-term debt is a single figure; the contract compares it with the "
        "derived current portion plus short-term borrowings.",
    },
)

#: Published SEC facts release fields to contract fields.
PUBLISHED_FACT_MAP: tuple[dict[str, Any], ...] = (
    {"published_field": "revenue", "field_id": "revenue", "unit": "USD"},
    {"published_field": "net_income", "field_id": "net_income_common", "unit": "USD"},
)


@dataclass
class CompanyReport:
    ticker: str
    cik: str | None = None
    identity_source: dict[str, Any] | None = None
    facts_origin: dict[str, Any] | None = None
    contract: dict[str, Any] | None = None
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    source_identity: list[dict[str, Any]] = field(default_factory=list)
    blocking_issues: list[str] = field(default_factory=list)
    state_counts: dict[str, int] = field(default_factory=dict)
    overall: str = sf.STATE_SOURCE_UNVALIDATED

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "cik": self.cik,
            "overall": self.overall,
            "state_counts": dict(self.state_counts),
            "identity_source": self.identity_source,
            "facts_origin": self.facts_origin,
            "source_identity": list(self.source_identity),
            "contract": self.contract,
            "comparisons": list(self.comparisons),
            "blocking_issues": list(self.blocking_issues),
        }


def _state_counts(contract: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for periods in (contract.get("field_states") or {}).values():
        for state in periods.values():
            counts[state] = counts.get(state, 0) + 1
    return dict(sorted(counts.items()))


def _contract_state_overall(result: sf.CompanyContractResult) -> str:
    counts: dict[str, int] = {}
    for fields in result.fields.values():
        for entry in fields.values():
            counts[entry.state] = counts.get(entry.state, 0) + 1
    if counts.get(sf.STATE_CONFLICT):
        return sf.STATE_CONFLICT
    usable = set(counts) - {sf.STATE_MISSING, sf.STATE_PERIOD_MISMATCH}
    if counts.get(sf.STATE_SOURCE_VALIDATED) and usable == {
        sf.STATE_SOURCE_VALIDATED,
        sf.STATE_MISSING,
    }:
        return sf.STATE_SOURCE_VALIDATED
    if sf.STATE_SOURCE_VALIDATED in counts and sf.STATE_SOURCE_UNVALIDATED in counts:
        return sf.STATE_SOURCE_PARTIALLY_VALIDATED
    if sf.STATE_SOURCE_UNVALIDATED in counts:
        return sf.STATE_SOURCE_UNVALIDATED
    if counts.get(sf.STATE_MISSING):
        return sf.STATE_MISSING
    return sf.STATE_SOURCE_UNVALIDATED


class RegistryReport:
    """Assemble the three-company reconciliation report."""

    def __init__(
        self,
        registry: FinancialDatasetRegistry,
        *,
        clock: Callable[[], str] | None = None,
        mode: str = "live",
    ):
        self.registry = registry
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.mode = mode
        self.families: list[dict[str, Any]] = []
        self.limitations: list[str] = []

    # -- source evidence ---------------------------------------------------
    def collect_sources(self, *, artifact_sample: int = 3) -> dict[str, ReleaseRef | None]:
        releases: dict[str, ReleaseRef | None] = {}
        inventory = self.registry.discover(
            "sec_bulk_archive", prefix="sec/bulk/", max_pages=1, limit=25, max_objects=25
        )
        self.families.append(inventory.as_dict())
        for family_id in ("serving_release_candidate", "serving_release_promoted"):
            entry: dict[str, Any] = {"family_id": family_id}
            try:
                release = self.registry.resolve_release(family_id)
                releases[family_id] = release
                sample = self.registry.sample_artifacts(release, limit=artifact_sample)
                entry.update(release.as_dict())
                entry["artifact_sample"] = sample
                if not sample["complete"]:
                    self.limitations.append(
                        f"{family_id}: sampled manifest artifacts are not stored in R2 "
                        f"({len(sample['missing'])} of {sample['sampled']} missing)"
                    )
            except TransportError as error:
                releases[family_id] = None
                entry["error"] = {"code": error.code, "message": error.message}
                self.limitations.append(f"{family_id}: {error.code} - {error.message}")
            self.families.append(entry)
        try:
            index, index_record = self.registry.valuation_index()
            self.families.append(
                {
                    "family_id": "valuation_packets",
                    "pointer": index_record.as_dict(),
                    "schema_version": index.get("schema_version"),
                    "companies": len(index.get("companies") or {}),
                    "findings": ["APPROVED_PACKET_INDEX_READ"],
                }
            )
        except TransportError as error:
            self.limitations.append(f"valuation_packets: {error.code} - {error.message}")
        try:
            sec_facts = self.registry.resolve_sec_facts_release()
        except TransportError as error:
            sec_facts = None
            self.limitations.append(f"sec_facts_release: {error.code} - {error.message}")
        releases["sec_facts_release"] = sec_facts
        if sec_facts is not None:
            entry = {
                "family_id": "sec_facts_release",
                "manifest_key": sec_facts.manifest_key,
                "release_id": sec_facts.release_id,
                "schema_version": sec_facts.schema_version,
                "manifest": sec_facts.manifest_record.as_dict(),
                "tickers": sec_facts.manifest.get("tickers"),
                "as_of": sec_facts.manifest.get("as_of"),
                "source_corpus": sec_facts.manifest.get("source_corpus"),
                "artifact_count": len(sec_facts.artifact_index),
                "findings": list(sec_facts.findings),
            }
            self.families.append(entry)
        return releases

    def serving_coverage(self, releases: dict[str, ReleaseRef | None]) -> dict[str, Any]:
        release = releases.get("serving_release_candidate")
        catalog: list[dict[str, Any]] = []
        record: ObjectRecord | None = None
        if release is not None:
            relative = "catalog/metrics.json"
            entry = release.artifact(relative)
            path = release.released_object_path(relative)
            if entry is not None:
                try:
                    payload, record = self.registry.read_object(
                        release.manifest_record.store, path, budget=256 * 1024
                    )
                    catalog = json.loads(payload)
                except TransportError as error:
                    self.limitations.append(f"serving metric catalog unreadable: {error.code}")
        by_id = {item.get("metric_id"): item for item in catalog if isinstance(item, dict)}
        coverage = []
        for entry in SERVING_CONTRACT_COVERAGE:
            metric = by_id.get(entry["serving_metric"]) if entry["serving_metric"] else None
            coverage.append(
                {
                    **entry,
                    "serving_metric_present": metric is not None,
                    "serving_metric_observations": metric.get("observations") if metric else None,
                    "serving_metric_units": metric.get("units") if metric else None,
                    "serving_metric_issuers": metric.get("issuer_count") if metric else None,
                }
            )
        supported = [item["field_id"] for item in coverage if item["state"] == "SUPPORTED"]
        return {
            "release_id": None if release is None else release.release_id,
            "catalog": record.as_dict() if record else None,
            "metric_count": len(catalog),
            "coverage": coverage,
            "supported_fields": supported,
            "unsupported_fields": [
                item["field_id"] for item in coverage if item["state"] != "SUPPORTED"
            ],
        }

    # -- per company -------------------------------------------------------
    def company(
        self, ticker: str, releases: dict[str, ReleaseRef | None], *, as_of: str, periods: int = 3
    ) -> CompanyReport:
        ticker = ticker.upper()
        report = CompanyReport(ticker=ticker)
        packet, packet_record = self._packet(ticker, report)
        cik = None
        if isinstance(packet, dict) and isinstance(packet.get("cik"), str):
            cik = packet["cik"]
            report.identity_source = {
                "kind": "approved_valuation_packet",
                "packet": None if packet_record is None else packet_record.as_dict(),
                "cik": cik,
                "cutoff_date": packet.get("cutoff_date"),
            }
        published, published_record, published_release = self._published(ticker, report)
        if cik is None and isinstance(published, dict) and published.get("cik"):
            cik = str(published["cik"])
            report.identity_source = {
                "kind": "published_sec_facts_release",
                "record": None if published_record is None else published_record.as_dict(),
                "cik": cik,
            }
        if cik is None:
            try:
                dataset_cik, dataset_record = self.registry.company_identity(ticker)
            except TransportError as error:
                dataset_cik, dataset_record = None, None
                report.blocking_issues.append(f"COMPANY_DATASET_IDENTITY_UNREADABLE:{error.code}")
            if dataset_cik:
                cik = dataset_cik
                report.identity_source = {
                    "kind": "per_company_dataset",
                    "record": None if dataset_record is None else dataset_record.as_dict(),
                    "cik": cik,
                }
        report.cik = cik
        if cik is None:
            report.blocking_issues.append("ISSUER_IDENTITY_UNRESOLVED")
            report.overall = sf.STATE_BLOCKED
            return report
        payload, facts_record, origin = self._facts(cik, releases, report)
        if payload is None or facts_record is None:
            report.blocking_issues.append("SOURCE_FACTS_UNAVAILABLE")
            report.overall = sf.STATE_BLOCKED
            return report
        report.facts_origin = {"origin": origin, "object": facts_record.as_dict()}
        try:
            observations = sf.parse_companyfacts(payload, source=facts_record.source_record())
        except ValueError as error:
            report.blocking_issues.append(f"SOURCE_FACTS_UNPARSEABLE:{error}")
            report.overall = sf.STATE_BLOCKED
            return report
        result = sf.evaluate_company(
            observations,
            ticker=ticker,
            cik=cik,
            as_of=as_of,
            periods=periods,
            source=facts_record.source_record(),
        )
        secondary = self._secondary_values(ticker, packet, published, result)
        sf.cross_validate(result, secondary)
        report.contract = result.as_dict()
        for fields in (report.contract.get("fields") or {}).values():
            for field_entry in fields.values():
                report.comparisons.extend(field_entry.get("cross_sources") or [])
        report.source_identity = self._source_identity(ticker, published, packet, facts_record)
        report.state_counts = _state_counts(report.contract)
        report.overall = _contract_state_overall(result)
        # A different source object is a provenance difference, not a value conflict:
        # value agreement is decided per field by the comparisons above.
        report.source_identity[:] = [identity for identity in report.source_identity]
        if not any(item["state"] == "AGREES" for item in report.comparisons):
            report.blocking_issues.append("NO_SECOND_SOURCE_CONFIRMATION")
        return report

    def _packet(
        self, ticker: str, report: CompanyReport
    ) -> tuple[dict[str, Any] | None, ObjectRecord | None]:
        try:
            return self.registry.valuation_packet(ticker)
        except TransportError as error:
            report.blocking_issues.append(f"APPROVED_PACKET_UNAVAILABLE:{error.code}")
            return None, None

    def _published(
        self, ticker: str, report: CompanyReport
    ) -> tuple[dict[str, Any] | None, ObjectRecord | None, ReleaseRef | None]:
        try:
            payload, record, release = self.registry.published_security(ticker)
        except TransportError as error:
            report.blocking_issues.append(f"PUBLISHED_SEC_FACTS_UNAVAILABLE:{error.code}")
            return None, None, None
        if record is not None and record.quality == QUALITY_ABSENT:
            report.blocking_issues.append("PUBLISHED_SEC_FACTS_NOT_IN_RELEASE")
        return payload, record, release

    def _facts(
        self,
        cik: str,
        releases: dict[str, ReleaseRef | None],
        report: CompanyReport,
    ) -> tuple[dict[str, Any] | None, ObjectRecord | None, str]:
        candidates = self.registry.corporate_facts_sources(
            cik, sec_facts_release=releases.get("sec_facts_release")
        )
        attempted: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                if candidate.get("member"):
                    payload, record = self.registry.read_zip_member(
                        candidate["store"], candidate["path"], candidate["member"]
                    )
                else:
                    payload, record = self.registry.read_object(
                        candidate["store"], candidate["path"], budget=8 * 1024 * 1024
                    )
                value = json.loads(payload)
                attempted.append(
                    {"candidate": candidate, "state": "OK", "object": record.as_dict()}
                )
                report.facts_origin = {"attempted": attempted}
                return value, record, candidate["origin"]
            except TransportError as error:
                attempted.append(
                    {"candidate": candidate, "state": error.code, "message": error.message}
                )
                if error.code not in {"OBJECT_NOT_FOUND", "ZIP_MEMBER_MISSING"}:
                    attempted[-1]["fatal"] = True
        report.facts_origin = {"attempted": attempted, "state": "FALLBACK_EXHAUSTED"}
        report.blocking_issues.append("SOURCE_FACTS_UNAVAILABLE")
        return None, None, ""

    def _secondary_values(
        self,
        ticker: str,
        packet: dict[str, Any] | None,
        published: dict[str, Any] | None,
        result: sf.CompanyContractResult,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if isinstance(packet, dict):
            packet_source = (
                f"approved packet {packet.get('schema_version')} cutoff {packet.get('cutoff_date')}"
            )
            historical = {
                row.get("period_end"): row
                for row in (packet.get("historical") or [])
                if isinstance(row, dict)
            }
            for period in result.periods:
                row = historical.get(period)
                if not row:
                    continue
                for mapping in PACKET_HISTORICAL_MAP:
                    field = (row.get("fields") or {}).get(mapping["packet_field"])
                    if not isinstance(field, dict) or field.get("value") is None:
                        continue
                    entries.append(
                        {
                            "field_id": mapping["field_id"],
                            "period_end": period,
                            "value": field["value"],
                            "unit": field.get("unit", mapping["unit"]),
                            "source": packet_source,
                            "basis": mapping.get("basis"),
                            "comparable": mapping["comparable"],
                        }
                    )
            bridge = packet.get("bridge") or {}
            for mapping in PACKET_BRIDGE_MAP:
                field = bridge.get(mapping["packet_field"])
                if not isinstance(field, dict) or field.get("value") is None:
                    continue
                period = field.get("period_end")
                if period not in result.periods:
                    continue
                entries.append(
                    {
                        "field_id": mapping["field_id"],
                        "period_end": period,
                        "value": field["value"],
                        "unit": field.get("unit", mapping["unit"]),
                        "source": packet_source,
                        "basis": mapping.get("basis", "packet balance-sheet bridge item"),
                        "comparable": mapping.get("comparable", True),
                    }
                )
        if isinstance(published, dict):
            facts = published.get("facts") or {}
            released = published.get("as_of")
            for mapping in PUBLISHED_FACT_MAP:
                fact = facts.get(mapping["published_field"])
                if not isinstance(fact, dict) or fact.get("value") is None:
                    continue
                target = result.fields.get(fact.get("end"), {}).get(mapping["field_id"])
                concept_matches = target is not None and target.concept == fact.get("concept")
                entries.append(
                    {
                        "field_id": mapping["field_id"],
                        "period_end": fact.get("end"),
                        "value": fact.get("value"),
                        "unit": fact.get("unit", mapping["unit"]),
                        "source": f"published SEC facts release as_of {released}",
                        "basis": f"published concept {fact.get('concept')}",
                        "comparable": concept_matches,
                    }
                )
        return entries

    def _source_identity(
        self,
        ticker: str,
        published: dict[str, Any] | None,
        packet: dict[str, Any] | None,
        facts_record: ObjectRecord,
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        if isinstance(published, dict):
            evidence = published.get("source_evidence") or {}
            expected = evidence.get("sha256")
            expected_key = evidence.get("key")
            if expected:
                same_object = (
                    isinstance(expected_key, str)
                    and expected_key.endswith(facts_record.path.split("/")[-1])
                    and facts_record.member is None
                )
                checks.append(
                    {
                        "check": "published_source_evidence_sha256",
                        "expected": expected,
                        "observed": facts_record.sha256,
                        "state": "MATCH"
                        if expected == facts_record.sha256
                        else "DIFFERENT_SOURCE_OBJECT",
                        "note": (
                            "published source object is the object this registry read"
                            if same_object
                            else "published evidence points at a different stored object; "
                            "values are compared but bytes are not identical"
                        ),
                    }
                )
        if isinstance(packet, dict):
            for field_id, value in (packet.get("bridge") or {}).items():
                if not isinstance(value, dict):
                    continue
                sec_source = (value.get("provenance") or {}).get("sec_source") or value.get(
                    "provenance", {}
                ).get("sec_source")
                if isinstance(sec_source, dict) and sec_source.get("sha256"):
                    checks.append(
                        {
                            "check": f"approved_packet_sec_source_{field_id}",
                            "expected": sec_source["sha256"],
                            "observed": facts_record.sha256,
                            "expected_member": sec_source.get("archive_member"),
                            "observed_member": facts_record.member,
                            "expected_crc32": sec_source.get("archive_member_crc32"),
                            "observed_crc32": facts_record.member_crc32,
                            "state": "MATCH"
                            if sec_source["sha256"] == facts_record.sha256
                            else "DIFFERENT_SOURCE_OBJECT",
                            "note": "approved packet references the archive member this "
                            "registry read"
                            if sec_source["sha256"] == facts_record.sha256
                            else "approval was built from a different source object; "
                            "compare values, not bytes",
                        }
                    )
                    break
        return checks

    # -- assembly ----------------------------------------------------------
    def build(
        self, tickers: Sequence[str], *, as_of: str, periods: int = 3, artifact_sample: int = 3
    ) -> dict[str, Any]:
        releases = self.collect_sources(artifact_sample=artifact_sample)
        coverage = self.serving_coverage(releases)
        companies = [
            self.company(ticker, releases, as_of=as_of, periods=periods) for ticker in tickers
        ]
        report: dict[str, Any] = {
            "schema_version": REPORT_SCHEMA,
            "generated_at": self.clock(),
            "mode": self.mode,
            "as_of": as_of,
            "contract": sf.CONTRACT_SCHEMA,
            "fields": [spec.field_id for spec in sf.CONTRACT_FIELDS] + list(sf.DERIVED_FIELDS),
            "sources": self.families,
            "serving_coverage": coverage,
            "companies": [company.as_dict() for company in companies],
            "quality_summary": {
                "companies": {
                    company.ticker: {
                        "overall": company.overall,
                        "state_counts": company.state_counts,
                    }
                    for company in companies
                },
                "states_used": sorted(
                    {state for company in companies for state in company.state_counts}
                ),
                "disclosed_states": list(sf.DISCLOSED_STATES),
            },
            "inventory": self.registry.inventory_report(),
            "limitations": self.limitations
            + [
                "Aggregate manifest row counts are not evidence that an individual "
                "observation is correct.",
                "Storage access does not establish availability dates, freshness or "
                "redistribution rights.",
                "Stored timestamps and ETags are not financial observation dates.",
            ],
        }
        report["report_sha256"] = sf.frame_report_hash(digest_body(report))
        return report


def digest_body(report: dict[str, Any]) -> dict[str, Any]:
    """The part of a report that the content digest covers.

    ``generated_at`` and the digest itself are excluded so the digest is a
    reproducible content address for the same inputs and as-of instant.
    """
    return {
        key: value for key, value in report.items() if key not in {"generated_at", "report_sha256"}
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    add(f"# Financial dataset registry report ({report['schema_version']})")
    add("")
    add(f"- Generated: `{report['generated_at']}`")
    add(f"- Mode: `{report['mode']}`")
    add(f"- As-of instant: `{report['as_of']}`")
    add(f"- Fundamentals contract: `{report['contract']}`")
    add(f"- Report digest: `{report['report_sha256']}`")
    add("")
    add("## Sources")
    add("")
    for source in report["sources"]:
        state = source.get("error", {}).get("code") if source.get("error") else "resolved"
        add(f"- `{source.get('family_id')}`: {state}")
        for finding in source.get("findings") or []:
            add(f"  - {finding}")
    add("")
    add("## Serving coverage of the contract")
    add("")
    add("| contract field | serving metric | state | notes |")
    add("| --- | --- | --- | --- |")
    for item in report["serving_coverage"]["coverage"]:
        add(
            f"| `{item['field_id']}` | {item['serving_metric'] or '—'} | "
            f"{item['state']} | {item['basis']} |"
        )
    add("")
    add("## Companies")
    add("")
    for company in report["companies"]:
        add(f"### {company['ticker']} (CIK {company.get('cik') or 'unresolved'})")
        add("")
        add(f"- Overall: `{company['overall']}`")
        add(f"- State counts: {json.dumps(company['state_counts'])}")
        if company.get("facts_origin"):
            add(f"- Facts origin: {json.dumps(company['facts_origin'], default=str)}")
        for identity in company.get("source_identity") or []:
            add(f"- Source identity `{identity['check']}`: {identity['state']}")
        for issue in company.get("blocking_issues") or []:
            add(f"- Blocker: `{issue}`")
        contract = company.get("contract")
        if not contract:
            add("")
            continue
        for period in contract["periods"]:
            add("")
            add(f"**Fiscal period ending {period}**")
            add("")
            add("| field | state | value | unit | concept |")
            add("| --- | --- | --- | --- | --- |")
            for field_id, entry in contract["fields"][period].items():
                add(
                    f"| `{field_id}` | {entry['state']} | {entry['value'] or '—'} | "
                    f"{entry['unit'] or '—'} | {entry['concept'] or '—'} |"
                )
            notes = []
            for field_id, entry in contract["fields"][period].items():
                if entry["state"] in (
                    sf.STATE_PASS,
                    sf.STATE_PASS_DERIVED,
                    sf.STATE_SOURCE_VALIDATED,
                ):
                    continue
                alternatives = [
                    item for item in entry["derivations"] if item.get("kind") == "alternative_tag"
                ]
                notes.append(f"- `{field_id}` {entry['state']}: {', '.join(entry['reason_codes'])}")
                for item in alternatives:
                    notes.append(
                        f"  - issuer tag `{item['concept']}` = {item['value']} {item['unit']} "
                        f"(filed {item['filed']}); not contract equivalent"
                    )
            if notes:
                add("")
                add("Field notes:")
                add("")
                lines.extend(notes)
        add("")
    add("## Limitations")
    add("")
    for limitation in report.get("limitations") or []:
        add(f"- {limitation}")
    return "\n".join(lines) + "\n"


def sanitize_for_markdown(report: dict[str, Any]) -> dict[str, Any]:
    """A copy of the report with the private object provenance removed.

    ``facts_origin`` and the read inventory name private store paths, object
    versions and hashes. Everything else the markdown renderer prints (values,
    concepts, states, reason codes, accessions, findings) is shareable.
    """
    sanitized = dict(report)
    sanitized.pop("inventory", None)
    sanitized["companies"] = [
        {key: value for key, value in company.items() if key != "facts_origin"}
        for company in report.get("companies") or []
    ]
    return sanitized


def render_sanitized_markdown(report: dict[str, Any]) -> str:
    """Shareable evidence: the same evaluated observations without private paths."""
    return render_markdown(sanitize_for_markdown(report))


def sanitize_summary(report: dict[str, Any]) -> dict[str, Any]:
    """A shareable summary with no private store paths or object hashes."""
    return {
        "schema_version": report["schema_version"],
        "mode": report["mode"],
        "as_of": report["as_of"],
        "contract": report["contract"],
        "report_sha256": report["report_sha256"],
        "source_families": [
            {
                "family_id": item.get("family_id"),
                "state": "error" if item.get("error") else "resolved",
                "findings": item.get("findings") or [],
                "artifact_sample_complete": (item.get("artifact_sample") or {}).get("complete"),
            }
            for item in report["sources"]
        ],
        "serving_coverage": {
            "supported_fields": report["serving_coverage"]["supported_fields"],
            "unsupported_fields": report["serving_coverage"]["unsupported_fields"],
            "metric_count": report["serving_coverage"]["metric_count"],
            "coverage": [
                {key: value for key, value in item.items() if key != "serving_metric_units"}
                for item in report["serving_coverage"]["coverage"]
            ],
        },
        "companies": [
            {
                "ticker": item["ticker"],
                "cik": item["cik"],
                "overall": item["overall"],
                "state_counts": item["state_counts"],
                "source_identity_states": [
                    {"check": check["check"].split("_")[0], "state": check["state"]}
                    for check in item.get("source_identity") or []
                ],
                "blocking_issues": item["blocking_issues"],
                "fields": {
                    period: {
                        field_id: {
                            "state": entry["state"],
                            "value": entry["value"],
                            "unit": entry["unit"],
                            "concept": entry["concept"],
                            "reason_codes": entry["reason_codes"],
                        }
                        for field_id, entry in fields.items()
                    }
                    for period, fields in ((item.get("contract") or {}).get("fields") or {}).items()
                },
            }
            for item in report["companies"]
        ],
        "limitations": report["limitations"],
    }


def write_report(
    report: dict[str, Any], output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> dict[str, str]:
    """Write the private report and a sanitized summary; returns written paths."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    private_path = target / "report.json"
    markdown_path = target / "report.md"
    summary_path = target / "summary.json"
    summary_markdown_path = target / "summary.md"
    inventory_path = target / "inventory.json"
    private_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(render_markdown(report))
    summary_path.write_text(json.dumps(sanitize_summary(report), indent=2, sort_keys=True) + "\n")
    summary_markdown_path.write_text(render_sanitized_markdown(report))
    inventory_path.write_text(json.dumps(report["inventory"], indent=2, sort_keys=True) + "\n")
    return {
        "report": str(private_path),
        "markdown": str(markdown_path),
        "summary": str(summary_path),
        "summary_markdown": str(summary_markdown_path),
        "inventory": str(inventory_path),
    }
