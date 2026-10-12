"""Baseline/candidate regression comparison for private registry reports.

This module answers a different question from ``report.py``. The report asks
*what is present and how well is it corroborated*; this module asks *what
changed since the approved baseline, and does the change mean anything*.

Two report generations of the same three issuers are compared by stable
observation keys (issuer CIK, contract field, fiscal period, unit, source
concept and accounting basis, and - where the input carries it - source object
revision, accession and availability cutoff). Every difference is classified
into a machine-readable finding with an explicit severity, so a scheduled or
operator run can decide automatically whether it is looking at a new filing
(``INFO``), a coverage or corroboration problem (``WARNING``), or broken
integrity, contradictory identity or an unexplained value change (``FAIL``).

A changed report digest is not by itself a regression: the digest covers
``generated_at``-free content, so it changes whenever anything at all moves.
Only classified findings decide the severity.

Outputs follow the same sensitivity split as ``report.py``: the private
comparison (``comparison.json`` / ``comparison.md``) carries object identity
and hashes, the sanitized pair (``summary.json`` / ``summary.md``) carries the
code, severity, period, concept, value and note only, and
``assert_no_private_metadata`` proves the sanitized payload is clean before it
is written.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from zion_terminal.datasets import sec_fundamentals as sf

COMPARISON_SCHEMA = "zion-financial-registry-comparison-v1"
DEFAULT_OUTPUT_DIR = Path("artifacts/registry-m2")

# -- severity ---------------------------------------------------------------
SEVERITY_PASS = "PASS"
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_FAIL = "FAIL"
SEVERITY_SCALE: tuple[str, ...] = (SEVERITY_PASS, SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_FAIL)
SEVERITY_RANK = {severity: rank for rank, severity in enumerate(SEVERITY_SCALE)}
SEVERITY_MEANING = {
    SEVERITY_PASS: "no material regression",
    SEVERITY_INFO: "expected new observations or documented source updates",
    SEVERITY_WARNING: "reduced coverage, staleness or an unresolved change",
    SEVERITY_FAIL: "broken integrity, contradictory identity, prohibited accounting "
    "substitution or a material unexplained regression",
}
#: CI exit status per overall severity. ``WARNING`` is non-zero so a scheduled
#: run surfaces an unresolved change instead of silencing it.
EXIT_STATUS = {
    SEVERITY_PASS: 0,
    SEVERITY_INFO: 0,
    SEVERITY_WARNING: 1,
    SEVERITY_FAIL: 2,
}

# -- explicit, documented thresholds ---------------------------------------
#: A numeric change below both bounds is a rounding/formatting difference, not a
#: regression. Both are documented and covered by tests.
VALUE_DRIFT_RELATIVE = Decimal("0.0005")  # 0.05% of the baseline value
VALUE_DRIFT_ABSOLUTE = Decimal("1000000")  # USD 1,000,000 in the field unit
#: Ratios that indicate an unconverted unit scale (thousands/millions/billions).
SCALE_SHIFT_FACTORS = (Decimal(1000), Decimal(10**6), Decimal(10**9))
#: Any availability instant after the report's own as-of instant is forward
#: looking. There is no grace window: point-in-time eligibility is a hard rule.
FUTURE_AVAILABILITY_GRACE = Decimal(0)

USABLE_STATES = frozenset(
    {sf.STATE_PASS, sf.STATE_PASS_DERIVED, sf.STATE_SOURCE_VALIDATED, sf.STATE_SOURCE_UNVALIDATED}
)
VALIDATED_STATES = frozenset({sf.STATE_PASS, sf.STATE_PASS_DERIVED, sf.STATE_SOURCE_VALIDATED})
#: Quality rank used to detect upgrades and downgrades without inventing an
#: order between unrelated states. Broken states rank below absence.
STATE_RANK = {
    sf.STATE_PASS: 5,
    sf.STATE_PASS_DERIVED: 4,
    sf.STATE_SOURCE_VALIDATED: 3,
    sf.STATE_SOURCE_UNVALIDATED: 2,
    sf.STATE_NOT_PIT_ELIGIBLE: 1,
    sf.STATE_PERIOD_MISMATCH: 1,
    sf.STATE_MISSING: 0,
    sf.STATE_NOT_CONFORMING: 0,
    sf.STATE_UNIT_MISMATCH: 0,
    sf.STATE_CONFLICT: 0,
    sf.STATE_BLOCKED: 0,
}
#: Explicit severity for each losing state, so the mapping is testable.
STATE_REGRESSION_CODES: dict[str, tuple[str, str]] = {
    sf.STATE_CONFLICT: ("EVIDENCE_CONFLICT_INTRODUCED", SEVERITY_FAIL),
    sf.STATE_BLOCKED: ("SOURCE_BLOCKED", SEVERITY_FAIL),
    sf.STATE_UNIT_MISMATCH: ("UNIT_MISMATCH_INTRODUCED", SEVERITY_FAIL),
    sf.STATE_NOT_CONFORMING: ("OBSERVATION_NO_LONGER_CONFORMS", SEVERITY_WARNING),
    sf.STATE_MISSING: ("OBSERVATION_LOST", SEVERITY_WARNING),
    sf.STATE_NOT_PIT_ELIGIBLE: ("POINT_IN_TIME_ELIGIBILITY_LOST", SEVERITY_WARNING),
    sf.STATE_PERIOD_MISMATCH: ("PERIOD_ALIGNMENT_LOST", SEVERITY_WARNING),
    sf.STATE_SOURCE_UNVALIDATED: ("VALIDATION_DOWNGRADE", SEVERITY_WARNING),
}
SIGN_CONVENTION_FIELDS = (
    "revenue",
    "depreciation_amortization",
    "capital_expenditure",
)
#: Input schema keys that make a payload the full private report rather than the
#: sanitized summary.
FULL_REPORT_MARKERS = ("companies", "inventory")

PUBLIC_INPUT_SCHEMAS = ("zion-financial-registry-report-v1",)

#: Markers that must never appear in a sanitized artifact. These are the private
#: store identifiers, object hashes, credentials and object paths that the
#: private report carries.
PRIVATE_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"s3://\S+"),
    re.compile(r"r2://\S+"),
    re.compile(r"\bsec-filing-intelligence/[\w./-]+"),
    re.compile(r"\bsec/bulk/[\w./-]+"),
    re.compile(r"\bgold/[\w./-]+"),
    re.compile(r"\bcompanyfacts\.json\b"),
    re.compile(r"\bCIK\d{10}\.json\b"),
    re.compile(r"\b[a-f0-9]{32,}\b"),
    re.compile(
        r"\b(?:financial-system-datasets|financial-data-wrds-private|"
        r"ppe-sec-intelligence-prod|financial-data-wrds-lakehouse)\b"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?key|secret|token|password)\b\s*[:=]"),
    re.compile(r'"(?:etag|sha256|member_crc32)"\s*:'),
)


class LeakageError(RuntimeError):
    """A sanitized artifact still carried private provenance."""


class IncomparableReports(ValueError):
    """The two inputs cannot be compared without inventing meaning."""


def assert_no_private_metadata(text: str, *, allowed_digests: Iterable[str] = ()) -> None:
    """Raise :class:`LeakageError` if a shareable payload carries private data.

    ``allowed_digests`` names content addresses that are intentionally published
    (the comparison's own digest); everything else that looks like an object
    hash, a store path or a credential is a leak.
    """
    scrubbed = text
    for digest in allowed_digests:
        if digest:
            scrubbed = scrubbed.replace(digest, "")
    for marker in PRIVATE_MARKERS:
        match = marker.search(scrubbed)
        if match is not None:
            raise LeakageError(
                f"private metadata marker {marker.pattern!r} found: {match.group(0)!r}"
            )


# -- value helpers ----------------------------------------------------------
def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _material(baseline: Decimal, candidate: Decimal) -> bool:
    """True when the change exceeds both the relative and absolute bounds."""
    delta = abs(candidate - baseline)
    if delta <= VALUE_DRIFT_ABSOLUTE:
        return False
    return delta > VALUE_DRIFT_RELATIVE * abs(baseline)


def _scale_factor(baseline: Decimal, candidate: Decimal) -> Decimal | None:
    if baseline == 0 or candidate == 0:
        return None
    ratio = abs(candidate) / abs(baseline)
    for factor in SCALE_SHIFT_FACTORS:
        if (ratio - factor).copy_abs() <= Decimal("0.0001") * factor:
            return factor
    for factor in SCALE_SHIFT_FACTORS:
        if (ratio - 1 / factor).copy_abs() <= Decimal("0.0001") / factor:
            return factor
    return None


def _concept_priority(field_id: str, concept: str | None) -> int | None:
    if concept is None or field_id not in sf.FIELD_IDS:
        return None
    spec = sf.field_spec(field_id)
    return spec.concepts.index(concept) if concept in spec.concepts else None


def _basis_changing_reason(field_id: str, concept: str | None) -> str | None:
    if concept is None or field_id not in sf.FIELD_IDS:
        return None
    spec = sf.field_spec(field_id)
    for candidate, reason in spec.basis_changing_concepts:
        if candidate == concept:
            return reason
    return None


def _contract_unit(field_id: str) -> str | None:
    return sf.field_spec(field_id).unit if field_id in sf.FIELD_IDS else None


# -- normalized views ------------------------------------------------------
@dataclass(frozen=True)
class Corroboration:
    source_label: str
    state: str

    @property
    def agrees(self) -> bool:
        return self.state == "AGREES"


@dataclass(frozen=True)
class IdentityCheck:
    check: str
    state: str


@dataclass(frozen=True)
class FieldView:
    ticker: str
    period_end: str
    field_id: str
    state: str
    value: Decimal | None = None
    unit: str | None = None
    concept: str | None = None
    reason_codes: tuple[str, ...] = ()
    accession: str | None = None
    filed: str | None = None
    available_at: str | None = None
    availability_evidence: str | None = None
    source_object: tuple[str, str, str, str | None] | None = None
    corroborations: tuple[Corroboration, ...] = ()
    provenance_available: bool = False

    @property
    def usable(self) -> bool:
        return self.state in USABLE_STATES

    @property
    def validated(self) -> bool:
        return self.state in VALIDATED_STATES

    @property
    def public(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "period_end": self.period_end,
            "state": self.state,
            "value": None if self.value is None else format(self.value, "f"),
            "unit": self.unit,
            "concept": self.concept,
        }


@dataclass
class CompanyView:
    ticker: str
    detail: str
    cik: str | None = None
    overall: str | None = None
    state_counts: dict[str, int] = field(default_factory=dict)
    periods: tuple[str, ...] = ()
    fields: dict[tuple[str, str], FieldView] = field(default_factory=dict)
    blocking_issues: tuple[str, ...] = ()
    identity_checks: tuple[IdentityCheck, ...] = ()

    def field_keys(self) -> set[tuple[str, str]]:
        return set(self.fields)

    def dropped_periods(self, other: CompanyView) -> list[str]:
        present = {period for period, _ in other.fields}
        return [period for period in self.periods if period not in present]


@dataclass
class FamilyView:
    family_id: str
    state: str
    findings: tuple[str, ...] = ()
    artifact_sample_complete: bool | None = None


@dataclass
class ReportView:
    label: str
    detail: str
    schema_version: str
    mode: str
    as_of: str | None
    contract: str | None
    digest: str | None
    companies: dict[str, CompanyView] = field(default_factory=dict)
    families: tuple[FamilyView, ...] = ()
    limitations: tuple[str, ...] = ()
    #: Tickers that resolve to more than one issuer CIK inside a single report.
    ticker_conflicts: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_report(cls, report: dict[str, Any], *, label: str) -> ReportView:
        if not isinstance(report, dict) or "companies" not in report:
            raise IncomparableReports(f"{label}: payload is not a registry report")
        detail = "sanitized_summary"
        companies: dict[str, CompanyView] = {}
        ciks: dict[str, set[str]] = {}
        for company in report.get("companies") or []:
            view = _company_view(company)
            if view.detail == "full_report":
                detail = "full_report"
            if view.cik:
                ciks.setdefault(view.ticker, set()).add(view.cik)
            companies.setdefault(view.ticker, view)
        conflicts = {
            ticker: tuple(sorted(values)) for ticker, values in ciks.items() if len(values) > 1
        }
        families = tuple(_family_views(report))
        return cls(
            label=label,
            detail=detail,
            schema_version=str(report.get("schema_version") or ""),
            mode=str(report.get("mode") or ""),
            as_of=report.get("as_of"),
            contract=report.get("contract"),
            digest=report.get("report_sha256"),
            companies=companies,
            families=families,
            limitations=tuple(report.get("limitations") or ()),
            ticker_conflicts=conflicts,
        )


def _company_view(company: dict[str, Any]) -> CompanyView:
    contract = company.get("contract") or {}
    fields_payload = contract.get("fields") or company.get("fields") or {}
    detail = "full_report" if contract.get("fields") else "sanitized_summary"
    periods = tuple(contract.get("periods") or sorted(fields_payload, reverse=True))
    ticker = str(company.get("ticker") or "").upper()
    view = CompanyView(
        ticker=ticker,
        detail=detail,
        cik=None if company.get("cik") is None else str(company.get("cik")),
        overall=company.get("overall"),
        state_counts={str(k): int(v) for k, v in (company.get("state_counts") or {}).items()},
        periods=periods,
        blocking_issues=tuple(company.get("blocking_issues") or ()),
        identity_checks=tuple(
            IdentityCheck(check=str(item.get("check")), state=str(item.get("state")))
            for item in (company.get("source_identity") or [])
        )
        + tuple(
            IdentityCheck(check=str(item.get("check")), state=str(item.get("state")))
            for item in (company.get("source_identity_states") or [])
        ),
    )
    company_object = contract.get("source") or {}
    for period, period_fields in fields_payload.items():
        for field_id, entry in (period_fields or {}).items():
            view.fields[(str(period), str(field_id))] = _field_view(
                ticker=ticker,
                period_end=str(period),
                field_id=str(field_id),
                entry=entry,
                company_object=company_object,
                detail=detail,
            )
    return view


def _field_view(
    *,
    ticker: str,
    period_end: str,
    field_id: str,
    entry: dict[str, Any],
    company_object: dict[str, Any],
    detail: str,
) -> FieldView:
    observation = entry.get("observation") or {}
    source = observation.get("source") or company_object or {}
    object_identity = None
    if source:
        object_identity = (
            str(source.get("store") or ""),
            str(source.get("path") or ""),
            str(source.get("sha256") or ""),
            None if source.get("member") is None else str(source.get("member")),
        )
    corroborations = tuple(
        Corroboration(
            source_label=str(item.get("secondary_source") or ""),
            state=str(item.get("state") or ""),
        )
        for item in (entry.get("cross_sources") or [])
    )
    return FieldView(
        ticker=ticker,
        period_end=period_end,
        field_id=field_id,
        state=str(entry.get("state") or ""),
        value=_decimal(entry.get("value")),
        unit=entry.get("unit"),
        concept=entry.get("concept"),
        reason_codes=tuple(entry.get("reason_codes") or ()),
        accession=None if observation.get("accession") is None else str(observation["accession"]),
        filed=None if observation.get("filed") is None else str(observation["filed"]),
        available_at=(
            None if observation.get("available_at") is None else str(observation["available_at"])
        ),
        availability_evidence=observation.get("availability_evidence"),
        source_object=object_identity,
        corroborations=corroborations,
        provenance_available=detail == "full_report",
    )


def _family_views(report: dict[str, Any]) -> list[FamilyView]:
    views: list[FamilyView] = []
    for item in report.get("sources") or []:
        error = item.get("error") or {}
        sample = item.get("artifact_sample") or {}
        views.append(
            FamilyView(
                family_id=str(item.get("family_id")),
                state="error" if error else "resolved",
                findings=tuple(item.get("findings") or ()),
                artifact_sample_complete=sample.get("complete"),
            )
        )
    for item in report.get("source_families") or []:
        views.append(
            FamilyView(
                family_id=str(item.get("family_id")),
                state=str(item.get("state") or ""),
                findings=tuple(item.get("findings") or ()),
                artifact_sample_complete=item.get("artifact_sample_complete"),
            )
        )
    return views


# -- findings --------------------------------------------------------------
def finding(
    code: str,
    severity: str,
    note: str,
    *,
    ticker: str | None = None,
    period_end: str | None = None,
    field_id: str | None = None,
    baseline: Any = None,
    candidate: Any = None,
    private: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if severity not in SEVERITY_RANK:
        raise ValueError(f"unknown severity {severity!r}")
    return {
        "code": code,
        "severity": severity,
        "note": note,
        "ticker": ticker,
        "period_end": period_end,
        "field_id": field_id,
        "baseline": baseline,
        "candidate": candidate,
        "private": private or {},
    }


def overall_severity(findings: Sequence[dict[str, Any]]) -> str:
    severity = SEVERITY_PASS
    for item in findings:
        if SEVERITY_RANK[item["severity"]] > SEVERITY_RANK[severity]:
            severity = item["severity"]
    return severity


def severity_counts(findings: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = {severity: 0 for severity in SEVERITY_SCALE}
    for item in findings:
        counts[item["severity"]] += 1
    return counts


def _corroboration_independence(company: CompanyView, view: FieldView) -> tuple[bool | None, str]:
    """Whether the candidate's corroboration is genuinely independent evidence.

    Agreement between two adapters that read the same underlying SEC source is
    not independent corroboration. The rule is deliberately conservative and
    uses the source-identity checks the report already records: a corroboration
    counts as independent only when the report shows a corroborating source
    built from a *different* stored object than the one read here.
    """
    if not view.provenance_available:
        return None, "sanitized input carries no per-field corroboration provenance"
    if not view.corroborations:
        return False, "no corroborating observation is recorded"
    if not any(item.agrees for item in view.corroborations):
        return False, "a corroborating observation is recorded but none agrees"
    distinct = [item for item in company.identity_checks if item.state == "DIFFERENT_SOURCE_OBJECT"]
    if distinct:
        return True, (
            "report records a corroborating source object distinct from the object read here "
            f"({len(distinct)} check(s))"
        )
    same = [item for item in company.identity_checks if item.state == "MATCH"]
    if same:
        return False, (
            "every recorded corroborating source references the same stored object this "
            "registry read; agreement on one source is not independent corroboration"
        )
    return None, "no source-identity evidence for the corroborating sources"


# -- comparison ------------------------------------------------------------
class RegistryComparison:
    """Compare two registry report generations and classify every difference."""

    def __init__(
        self,
        *,
        clock: Callable[[], str] | None = None,
        allow_mode_mismatch: bool = False,
        allow_contract_mismatch: bool = False,
    ):
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.allow_mode_mismatch = allow_mode_mismatch
        self.allow_contract_mismatch = allow_contract_mismatch
        self.findings: list[dict[str, Any]] = []
        self.skipped: list[str] = []
        self.candidate_as_of: str | None = None

    # -- entry point -------------------------------------------------------
    def compare(self, baseline: ReportView, candidate: ReportView) -> dict[str, Any]:
        self.candidate_as_of = candidate.as_of
        self._check_comparable(baseline, candidate)
        self._compare_sources(baseline, candidate)
        for ticker in sorted(set(baseline.companies) | set(candidate.companies)):
            self._compare_company(baseline.companies.get(ticker), candidate.companies.get(ticker))
        return self.build(baseline, candidate)

    # -- guardrails --------------------------------------------------------
    def _check_comparable(self, baseline: ReportView, candidate: ReportView) -> None:
        if baseline.contract and candidate.contract and baseline.contract != candidate.contract:
            message = (
                f"contract schema mismatch: baseline {baseline.contract} vs "
                f"candidate {candidate.contract}"
            )
            if not self.allow_contract_mismatch:
                raise IncomparableReports(message)
            self.findings.append(finding("CONTRACT_SCHEMA_MISMATCH", SEVERITY_FAIL, message))
        if baseline.mode != candidate.mode:
            message = f"mode mismatch: baseline {baseline.mode} vs candidate {candidate.mode}"
            if not self.allow_mode_mismatch:
                raise IncomparableReports(message)
            self.findings.append(finding("MODE_MISMATCH", SEVERITY_FAIL, message))
        if baseline.digest and candidate.digest and baseline.digest != candidate.digest:
            # A moved content address is not a regression by itself.
            self.findings.append(
                finding(
                    "REPORT_CONTENT_ADDRESS_CHANGED",
                    SEVERITY_INFO,
                    "the report content address changed; severity is decided by the "
                    "classified findings below, never by the address alone",
                )
            )
        if baseline.as_of and candidate.as_of and candidate.as_of < baseline.as_of:
            self.findings.append(
                finding(
                    "AS_OF_REGRESSION",
                    SEVERITY_WARNING,
                    "the candidate as-of instant is earlier than the baseline's; an earlier "
                    "cutoff can only shrink point-in-time coverage",
                    baseline=baseline.as_of,
                    candidate=candidate.as_of,
                )
            )
        for view in (baseline, candidate):
            if view.detail == "sanitized_summary":
                self._skip(
                    f"{view.label}: sanitized input - provenance checks "
                    "(source object revision, corroboration independence) are not evaluable"
                )

    def _skip(self, reason: str) -> None:
        if reason not in self.skipped:
            self.skipped.append(reason)

    def _compare_identity_conflicts(self, view: ReportView) -> None:
        for ticker, ciks in sorted(view.ticker_conflicts.items()):
            self.findings.append(
                finding(
                    "TICKER_IDENTITY_COLLISION",
                    SEVERITY_FAIL,
                    f"{view.label}: ticker {ticker} resolves to more than one issuer CIK "
                    f"({', '.join(ciks)}); issuer identity is contradictory",
                    ticker=ticker,
                    candidate=list(ciks),
                )
            )

    def _compare_sources(self, baseline: ReportView, candidate: ReportView) -> None:
        self._compare_identity_conflicts(baseline)
        self._compare_identity_conflicts(candidate)
        if not baseline.families or not candidate.families:
            self._skip("source family inventory is absent from at least one input")
            return
        base = {item.family_id: item for item in baseline.families}
        cand = {item.family_id: item for item in candidate.families}
        for family_id in sorted(set(base) | set(cand)):
            before, after = base.get(family_id), cand.get(family_id)
            if before is None or after is None:
                continue
            if before.state == "resolved" and after.state != "resolved":
                self.findings.append(
                    finding(
                        "SOURCE_FAMILY_UNRESOLVED",
                        SEVERITY_FAIL,
                        f"source family {family_id} resolved in the baseline and does not "
                        "resolve in the candidate",
                        baseline=before.state,
                        candidate=after.state,
                    )
                )
            elif before.state != "resolved" and after.state == "resolved":
                self.findings.append(
                    finding(
                        "SOURCE_FAMILY_RECOVERED",
                        SEVERITY_INFO,
                        f"source family {family_id} resolves again",
                        baseline=before.state,
                        candidate=after.state,
                    )
                )
            if before.artifact_sample_complete is True and after.artifact_sample_complete is False:
                self.findings.append(
                    finding(
                        "MANIFEST_ARTIFACT_SAMPLE_INCOMPLETE",
                        SEVERITY_FAIL,
                        f"manifest artifacts for {family_id} were sampled in the baseline and "
                        "are missing from storage in the candidate",
                        baseline=True,
                        candidate=False,
                    )
                )

    def _compare_company(self, baseline: CompanyView | None, candidate: CompanyView | None) -> None:
        if baseline is None and candidate is None:
            return
        ticker = baseline.ticker if baseline is not None else candidate.ticker  # type: ignore[union-attr]
        if baseline is None:
            self.findings.append(
                finding(
                    "COMPANY_ADDED",
                    SEVERITY_INFO,
                    f"{ticker} appears in the candidate and not in the baseline",
                    ticker=ticker,
                )
            )
            return
        if candidate is None:
            self.findings.append(
                finding(
                    "COMPANY_DROPPED",
                    SEVERITY_WARNING,
                    f"{ticker} is in the baseline and missing from the candidate",
                    ticker=ticker,
                )
            )
            return
        self._compare_identity(baseline, candidate)
        self._compare_coverage(baseline, candidate)
        for key in sorted(baseline.field_keys() | candidate.field_keys()):
            period_end, field_id = key
            self._compare_field(
                baseline, candidate, baseline.fields.get(key), candidate.fields.get(key)
            )
        self._compare_blockers(baseline, candidate)
        self._note_known_conditions(baseline, candidate)

    def _compare_identity(self, baseline: CompanyView, candidate: CompanyView) -> None:
        ticker = baseline.ticker
        if baseline.cik and candidate.cik and baseline.cik != candidate.cik:
            self.findings.append(
                finding(
                    "ISSUER_IDENTITY_CHANGED",
                    SEVERITY_FAIL,
                    "the same ticker resolves to a different issuer CIK; issuer identity is "
                    "contradictory and the two reports are not comparable per issuer",
                    ticker=ticker,
                    baseline=baseline.cik,
                    candidate=candidate.cik,
                )
            )
        elif baseline.cik and not candidate.cik:
            self.findings.append(
                finding(
                    "ISSUER_IDENTITY_UNRESOLVED",
                    SEVERITY_FAIL,
                    "issuer CIK resolved in the baseline and does not resolve in the candidate",
                    ticker=ticker,
                    baseline=baseline.cik,
                )
            )
        base_checks = {item.check: item.state for item in baseline.identity_checks}
        cand_checks = {item.check: item.state for item in candidate.identity_checks}
        for check in sorted(set(base_checks) | set(cand_checks)):
            before, after = base_checks.get(check), cand_checks.get(check)
            if before == "MATCH" and after == "DIFFERENT_SOURCE_OBJECT":
                self.findings.append(
                    finding(
                        "SOURCE_IDENTITY_REGRESSION",
                        SEVERITY_WARNING,
                        f"source identity check {check} no longer points at the object read here",
                        ticker=ticker,
                        baseline=before,
                        candidate=after,
                    )
                )
            elif before is None and after == "DIFFERENT_SOURCE_OBJECT":
                self.findings.append(
                    finding(
                        "SOURCE_IDENTITY_DIVERGENCE_OBSERVED",
                        SEVERITY_INFO,
                        f"new source identity check {check} reports a different source object",
                        ticker=ticker,
                        candidate=after,
                    )
                )

    def _compare_coverage(self, baseline: CompanyView, candidate: CompanyView) -> None:
        ticker = baseline.ticker
        lost = baseline.dropped_periods(candidate)
        if lost:
            self.findings.append(
                finding(
                    "PERIOD_OBSERVED_LOST",
                    SEVERITY_WARNING,
                    "fiscal periods present in the baseline are absent from the candidate: "
                    + ", ".join(lost),
                    ticker=ticker,
                    baseline=list(baseline.periods),
                    candidate=list(candidate.periods),
                )
            )
        added = [period for period in candidate.periods if period not in baseline.periods]
        if added:
            self.findings.append(
                finding(
                    "PERIOD_OBSERVED_ADDED",
                    SEVERITY_INFO,
                    "new fiscal periods are available: " + ", ".join(added),
                    ticker=ticker,
                    candidate=list(added),
                )
            )
        if baseline.overall and candidate.overall and baseline.overall != candidate.overall:
            before_rank = _overall_rank(baseline.overall)
            after_rank = _overall_rank(candidate.overall)
            if candidate.overall in {sf.STATE_CONFLICT, sf.STATE_BLOCKED}:
                severity = SEVERITY_FAIL
            elif after_rank < before_rank:
                severity = SEVERITY_WARNING
            else:
                severity = SEVERITY_INFO
            self.findings.append(
                finding(
                    _overall_code(baseline.overall, candidate.overall),
                    severity,
                    f"company overall quality moved from {baseline.overall} to {candidate.overall}",
                    ticker=ticker,
                    baseline=baseline.overall,
                    candidate=candidate.overall,
                )
            )

    def _compare_blockers(self, baseline: CompanyView, candidate: CompanyView) -> None:
        ticker = baseline.ticker
        added = sorted(set(candidate.blocking_issues) - set(baseline.blocking_issues))
        resolved = sorted(set(baseline.blocking_issues) - set(candidate.blocking_issues))
        for issue in added:
            self.findings.append(
                finding(
                    "BLOCKING_ISSUE_ADDED",
                    SEVERITY_FAIL,
                    f"new blocking issue {issue}",
                    ticker=ticker,
                    candidate=issue,
                )
            )
        for issue in resolved:
            self.findings.append(
                finding(
                    "BLOCKING_ISSUE_RESOLVED",
                    SEVERITY_INFO,
                    f"blocking issue {issue} no longer present",
                    ticker=ticker,
                    baseline=issue,
                )
            )
        for issue in sorted(set(baseline.blocking_issues) & set(candidate.blocking_issues)):
            self._skip(f"{ticker}: blocking issue {issue} persists unchanged")

    def _compare_field(
        self,
        company: CompanyView,
        candidate_company: CompanyView,
        baseline: FieldView | None,
        candidate: FieldView | None,
    ) -> None:
        ticker = company.ticker
        if baseline is None and candidate is not None:
            new_period = candidate.period_end not in company.periods
            self.findings.append(
                finding(
                    "NEW_PERIOD_FIELD_OBSERVED" if new_period else "FIELD_GAINED",
                    SEVERITY_INFO,
                    "field present in the candidate and not in the baseline"
                    + (" for a newly observed fiscal period" if new_period else ""),
                    ticker=ticker,
                    period_end=candidate.period_end,
                    field_id=candidate.field_id,
                    candidate=candidate.public,
                )
            )
            return
        if candidate is None and baseline is not None:
            if baseline.period_end not in candidate_company.periods:
                return  # already reported as PERIOD_OBSERVED_LOST
            if baseline.usable:
                self.findings.append(
                    finding(
                        "FIELD_LOST",
                        SEVERITY_WARNING,
                        "a previously available fundamental field is no longer present for this "
                        "period; coverage is reduced",
                        ticker=ticker,
                        period_end=baseline.period_end,
                        field_id=baseline.field_id,
                        baseline=baseline.public,
                    )
                )
            else:
                self.findings.append(
                    finding(
                        "UNUSABLE_FIELD_REMOVED",
                        SEVERITY_INFO,
                        "a previously unusable field entry is no longer present",
                        ticker=ticker,
                        period_end=baseline.period_end,
                        field_id=baseline.field_id,
                        baseline=baseline.public,
                    )
                )
            return
        assert baseline is not None and candidate is not None
        self.findings.extend(
            _field_findings(candidate_company, baseline, candidate, as_of=self.candidate_as_of)
        )
        # Independence is evidence about the *candidate* report, so the candidate
        # company's source-identity checks decide it.
        self._check_corroboration(candidate_company, baseline, candidate)

    def _check_corroboration(
        self, company: CompanyView, baseline: FieldView, candidate: FieldView
    ) -> None:
        """A validation claim must rest on evidence, and on independent evidence.

        A state that is newly validated on evidence from the same stored object the
        registry already read is a false upgrade and fails; the same condition
        carried over unchanged from the baseline is recorded as an unresolved
        condition, never as a silent pass.
        """
        ticker = company.ticker
        common = {
            "ticker": ticker,
            "period_end": candidate.period_end,
            "field_id": candidate.field_id,
        }
        upgraded = not baseline.validated and candidate.validated
        independent, reason = _corroboration_independence(company, candidate)
        if candidate.state in {sf.STATE_SOURCE_VALIDATED, sf.STATE_PASS} and independent is False:
            self.findings.append(
                finding(
                    "NON_INDEPENDENT_CORROBORATION"
                    if upgraded
                    else "NON_INDEPENDENT_CORROBORATION_PREEXISTING",
                    SEVERITY_FAIL if upgraded else SEVERITY_WARNING,
                    (
                        "a data-quality state is asserted on evidence that is not independent: "
                        + reason
                    )
                    if upgraded
                    else "an existing validation state rests on evidence that is not independent: "
                    + reason,
                    baseline=baseline.public,
                    candidate=candidate.public,
                    private={
                        "corroborations": [item.source_label for item in candidate.corroborations]
                    },
                    **common,
                )
            )
        elif upgraded and independent is None:
            self._skip(
                f"{ticker} {candidate.field_id} {candidate.period_end}: validation upgrade could "
                f"not be checked for independence ({reason})"
            )

    def _note_known_conditions(self, baseline: CompanyView, candidate: CompanyView) -> None:
        unchanged = {
            key: view
            for key, view in candidate.fields.items()
            if key in baseline.fields
            and baseline.fields[key].state == view.state
            and view.state not in USABLE_STATES
        }
        for key in sorted(unchanged):
            view = candidate.fields[key]
            self._skip(
                f"{candidate.ticker} {view.field_id} {view.period_end}: {view.state} unchanged "
                "from the baseline"
            )

    # -- assembly ----------------------------------------------------------
    def build(self, baseline: ReportView, candidate: ReportView) -> dict[str, Any]:
        companies: list[dict[str, Any]] = []
        for ticker in sorted(set(baseline.companies) | set(candidate.companies)):
            items = [item for item in self.findings if item["ticker"] == ticker]
            before = baseline.companies.get(ticker)
            after = candidate.companies.get(ticker)
            companies.append(
                {
                    "ticker": ticker,
                    "cik": {
                        "baseline": None if before is None else before.cik,
                        "candidate": None if after is None else after.cik,
                    },
                    "severity": overall_severity(items),
                    "quality": {
                        "overall": {
                            "baseline": None if before is None else before.overall,
                            "candidate": None if after is None else after.overall,
                        },
                        "state_counts": {
                            "baseline": {} if before is None else before.state_counts,
                            "candidate": {} if after is None else after.state_counts,
                        },
                        "periods": {
                            "baseline": [] if before is None else list(before.periods),
                            "candidate": [] if after is None else list(after.periods),
                        },
                    },
                    "finding_counts": severity_counts(items),
                    "findings": items,
                }
            )
        report_level = [item for item in self.findings if item["ticker"] is None]
        result: dict[str, Any] = {
            "schema_version": COMPARISON_SCHEMA,
            "generated_at": self.clock(),
            "baseline": _view_summary(baseline),
            "candidate": _view_summary(candidate),
            "contract": candidate.contract or baseline.contract,
            "as_of": {"baseline": baseline.as_of, "candidate": candidate.as_of},
            "thresholds": {
                "value_drift_relative": format(VALUE_DRIFT_RELATIVE, "f"),
                "value_drift_absolute": format(VALUE_DRIFT_ABSOLUTE, "f"),
                "scale_shift_factors": [str(int(factor)) for factor in SCALE_SHIFT_FACTORS],
                "future_availability_grace": format(FUTURE_AVAILABILITY_GRACE, "f"),
                "removal_rule": "a change is immaterial only when it is within BOTH bounds",
            },
            "severity_scale": {severity: SEVERITY_MEANING[severity] for severity in SEVERITY_SCALE},
            "exit_status": {severity: status for severity, status in EXIT_STATUS.items()},
            "overall_severity": overall_severity(self.findings),
            "report_level": {
                "finding_counts": severity_counts(report_level),
                "findings": report_level,
            },
            "companies": companies,
            "finding_counts": severity_counts(self.findings),
            "findings_total": len(self.findings),
            "skipped_checks": list(self.skipped),
            "interpretation_rules": [
                "A changed report content address is never a regression by itself.",
                "Agreement between two adapters that read the same underlying SEC source is not "
                "independent corroboration and never promotes a state by itself.",
                "A value change is explained only by a new accession/filing or a changed source "
                "object revision; anything else is unexplained.",
                "Issuer CIK, concept, unit and fiscal period identify an observation; a change in "
                "any of them is a different observation, not a restated one.",
            ],
        }
        result["comparison_sha256"] = sf.frame_report_hash(_digest_body(result))
        return result


def _overall_rank(state: str) -> int:
    return {
        sf.STATE_SOURCE_VALIDATED: 4,
        sf.STATE_SOURCE_PARTIALLY_VALIDATED: 3,
        sf.STATE_SOURCE_UNVALIDATED: 2,
        sf.STATE_MISSING: 1,
        sf.STATE_BLOCKED: 0,
        sf.STATE_CONFLICT: 0,
    }.get(state, 2)


def _overall_code(baseline: str, candidate: str) -> str:
    if _overall_rank(candidate) < _overall_rank(baseline):
        return "COMPANY_OVERALL_DOWNGRADE"
    if _overall_rank(candidate) > _overall_rank(baseline):
        return "COMPANY_OVERALL_UPGRADE"
    return "COMPANY_OVERALL_CHANGED"


def _view_summary(view: ReportView) -> dict[str, Any]:
    return {
        "label": view.label,
        "detail_level": view.detail,
        "schema_version": view.schema_version,
        "mode": view.mode,
        "as_of": view.as_of,
        "contract": view.contract,
        "company_count": len(view.companies),
        "tickers": sorted(view.companies),
        "provenance_available": view.detail == "full_report",
    }


def _field_findings(
    company: CompanyView,
    baseline: FieldView,
    candidate: FieldView,
    *,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    """Classify every observable difference for one (period, field) pair."""
    out: list[dict[str, Any]] = []
    ticker = company.ticker
    common: dict[str, Any] = {
        "ticker": ticker,
        "period_end": candidate.period_end,
        "field_id": candidate.field_id,
    }
    private = {
        "baseline_object": baseline.source_object,
        "candidate_object": candidate.source_object,
    }
    # 1. accounting basis identity: concept and unit. A lost observation has no
    # concept or unit to compare; the state finding already records the loss.
    observation_lost = candidate.state == sf.STATE_MISSING and candidate.concept is None
    if not observation_lost and candidate.unit != baseline.unit:
        contract_unit = _contract_unit(candidate.field_id)
        if contract_unit is not None and candidate.unit != contract_unit:
            out.append(
                finding(
                    "CONTRACT_UNIT_VIOLATION",
                    SEVERITY_FAIL,
                    f"observed unit {candidate.unit!r} is outside the contract unit "
                    f"{contract_unit!r} for this field",
                    baseline=baseline.unit,
                    candidate=candidate.unit,
                    private=private,
                    **common,
                )
            )
        elif contract_unit is not None and baseline.unit != contract_unit:
            out.append(
                finding(
                    "UNIT_CORRECTED",
                    SEVERITY_INFO,
                    f"unit is now the contract unit {contract_unit!r}",
                    baseline=baseline.unit,
                    candidate=candidate.unit,
                    **common,
                )
            )
        else:
            out.append(
                finding(
                    "UNIT_CHANGED",
                    SEVERITY_WARNING,
                    "unit changed without a contract unit violation; values are not comparable "
                    "until the change is explained",
                    baseline=baseline.unit,
                    candidate=candidate.unit,
                    private=private,
                    **common,
                )
            )
    if not observation_lost and candidate.concept != baseline.concept:
        basis_reason = _basis_changing_reason(candidate.field_id, candidate.concept)
        before_priority = _concept_priority(baseline.field_id, baseline.concept)
        after_priority = _concept_priority(candidate.field_id, candidate.concept)
        if basis_reason is not None:
            out.append(
                finding(
                    "PROHIBITED_BASIS_SUBSTITUTION",
                    SEVERITY_FAIL,
                    f"the reported concept changed to {candidate.concept!r}, which changes "
                    f"what the field measures ({basis_reason}); the value is not the same measure",
                    baseline=baseline.concept,
                    candidate=candidate.concept,
                    private=private,
                    **common,
                )
            )
        elif after_priority is None and candidate.usable:
            out.append(
                finding(
                    "BASIS_SUBSTITUTION_UNSUPPORTED",
                    SEVERITY_FAIL,
                    f"concept {candidate.concept!r} is not a contract concept for this field but "
                    "is reported as a usable observation; new evidence conflicts with the "
                    "established definition",
                    baseline=baseline.concept,
                    candidate=candidate.concept,
                    private=private,
                    **common,
                )
            )
        elif (
            before_priority is not None
            and after_priority is not None
            and after_priority > before_priority
        ):
            out.append(
                finding(
                    "CONCEPT_PRIORITY_DEMOTED",
                    SEVERITY_WARNING,
                    f"contract concept moved from {baseline.concept!r} to the lower-priority "
                    f"{candidate.concept!r}",
                    baseline=baseline.concept,
                    candidate=candidate.concept,
                    private=private,
                    **common,
                )
            )
        else:
            out.append(
                finding(
                    "CONCEPT_CHANGED",
                    SEVERITY_INFO,
                    f"reported concept changed from {baseline.concept!r} to {candidate.concept!r} "
                    "within the same basis",
                    baseline=baseline.concept,
                    candidate=candidate.concept,
                    private=private,
                    **common,
                )
            )
    # 2. numeric change with an explicit explanation rule.
    if baseline.value is not None and candidate.value is not None:
        if candidate.value != baseline.value:
            delta = candidate.value - baseline.value
            rel = Decimal(0) if baseline.value == 0 else abs(delta) / abs(baseline.value)
            same_filing = (
                baseline.accession is not None
                and candidate.accession is not None
                and baseline.accession == candidate.accession
            )
            source_revision_changed = (
                baseline.source_object is not None
                and candidate.source_object is not None
                and baseline.source_object != candidate.source_object
            )
            details = {
                "delta": format(delta, "f"),
                "relative": format(rel, "f"),
                "baseline_filing": baseline.accession,
                "candidate_filing": candidate.accession,
                "source_object_changed": source_revision_changed,
            }
            if not _material(baseline.value, candidate.value):
                out.append(
                    finding(
                        "IMMATERIAL_VALUE_DRIFT",
                        SEVERITY_INFO,
                        "value moved within the documented immaterial bounds",
                        baseline=baseline.public,
                        candidate=candidate.public,
                        private={**private, **details},
                        **common,
                    )
                )
            else:
                factor = _scale_factor(baseline.value, candidate.value)
                if factor is not None and candidate.unit == baseline.unit:
                    out.append(
                        finding(
                            "SUSPECTED_UNCONVERTED_SCALE",
                            SEVERITY_FAIL,
                            f"value moved by a factor of {factor} with an unchanged unit; this is "
                            "an unconverted unit scale, not a restatement",
                            baseline=baseline.public,
                            candidate=candidate.public,
                            private={**private, **details},
                            **common,
                        )
                    )
                elif (
                    baseline.accession is not None
                    and candidate.accession is not None
                    and not same_filing
                ):
                    if _later_filing(candidate, baseline):
                        out.append(
                            finding(
                                "RESTATEMENT_WITH_NEW_FILING",
                                SEVERITY_INFO,
                                "value changed under a later filing/accession for the same fiscal "
                                "period; treated as a permitted restatement",
                                baseline=baseline.public,
                                candidate=candidate.public,
                                private={**private, **details},
                                **common,
                            )
                        )
                    else:
                        out.append(
                            finding(
                                "FILING_REGRESSION_EARLIER_ACCESSION",
                                SEVERITY_FAIL,
                                "value changed and the candidate observation comes from an earlier "
                                "filing than the baseline",
                                baseline=baseline.public,
                                candidate=candidate.public,
                                private={**private, **details},
                                **common,
                            )
                        )
                elif same_filing and source_revision_changed:
                    out.append(
                        finding(
                            "VALUE_CHANGED_WITH_SOURCE_REVISION",
                            SEVERITY_WARNING,
                            "value changed under the same accession while the stored source object "
                            "revision also changed; the source side must be explained",
                            baseline=baseline.public,
                            candidate=candidate.public,
                            private={**private, **details},
                            **common,
                        )
                    )
                else:
                    out.append(
                        finding(
                            "VALUE_CHANGED_WITHOUT_SOURCE_REVISION",
                            SEVERITY_FAIL,
                            "value changed with no identified new filing and no identified source "
                            "object revision: unexplained regression",
                            baseline=baseline.public,
                            candidate=candidate.public,
                            private={**private, **details},
                            **common,
                        )
                    )
        if (
            baseline.value > 0
            and candidate.value < 0
            and candidate.field_id in SIGN_CONVENTION_FIELDS
        ):
            out.append(
                finding(
                    "SIGN_CONVENTION_VIOLATION",
                    SEVERITY_FAIL,
                    "sign flipped against the documented sign convention for this field",
                    baseline=baseline.public,
                    candidate=candidate.public,
                    private=private,
                    **common,
                )
            )
    elif baseline.value is not None and candidate.value is None and candidate.usable:
        out.append(
            finding(
                "VALUE_LOST_WITH_USABLE_STATE",
                SEVERITY_FAIL,
                "state claims a usable observation while the value is absent",
                baseline=baseline.public,
                candidate=candidate.public,
                **common,
            )
        )
    # 3. availability cutoff integrity, including forward-looking availability.
    out.extend(_availability_findings(baseline, candidate, common))
    out.extend(_forward_availability_findings(candidate, common, as_of=as_of))
    # 4. state movement.
    out.extend(_state_findings(baseline, candidate, common))
    return out


def _forward_availability_findings(
    candidate: FieldView, common: dict[str, Any], *, as_of: str | None
) -> list[dict[str, Any]]:
    """An observation claimed usable before it was available is not usable.

    The comparison uses the same date-only, string-ordered point-in-time
    convention as :func:`sec_fundamentals.evaluate_company`: an availability
    instant that sorts after the report's own as-of instant is forward looking.
    """
    if not as_of or not candidate.available_at or not candidate.usable:
        return []
    if candidate.available_at <= as_of:
        return []
    return [
        finding(
            "FUTURE_AVAILABILITY_UNSUPPORTED",
            SEVERITY_FAIL,
            "the observation claims a usable state while its availability instant is after the "
            "candidate report's own as-of instant",
            baseline=None,
            candidate={
                "available_at": candidate.available_at,
                "as_of": as_of,
                "state": candidate.state,
            },
            **common,
        )
    ]


def _later_filing(candidate: FieldView, baseline: FieldView) -> bool:
    """True when the candidate observation is documented as newer than the baseline."""
    if candidate.filed and baseline.filed:
        return candidate.filed > baseline.filed
    if candidate.available_at and baseline.available_at:
        return candidate.available_at >= baseline.available_at
    return False


def _availability_findings(
    baseline: FieldView, candidate: FieldView, common: dict[str, Any]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    same_filing = (
        baseline.accession is not None
        and candidate.accession is not None
        and baseline.accession == candidate.accession
    )
    if same_filing and baseline.available_at and candidate.available_at:
        if candidate.available_at < baseline.available_at:
            out.append(
                finding(
                    "AVAILABILITY_CUTOFF_INCONSISTENT",
                    SEVERITY_FAIL,
                    "the availability instant moved earlier for the same accession; filing and "
                    "availability dates are inconsistent",
                    baseline=baseline.available_at,
                    candidate=candidate.available_at,
                    **common,
                )
            )
    if same_filing and baseline.filed and candidate.filed and candidate.filed != baseline.filed:
        out.append(
            finding(
                "FILING_DATE_INCONSISTENT",
                SEVERITY_FAIL,
                "the filing date changed for the same accession",
                baseline=baseline.filed,
                candidate=candidate.filed,
                **common,
            )
        )
    return out


def _state_findings(
    baseline: FieldView, candidate: FieldView, common: dict[str, Any]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if candidate.state == baseline.state:
        return out
    before = STATE_RANK.get(baseline.state, 0)
    after = STATE_RANK.get(candidate.state, 0)
    if after < before:
        code, severity = STATE_REGRESSION_CODES.get(
            candidate.state, ("STATE_REGRESSION", SEVERITY_WARNING)
        )
        out.append(
            finding(
                code,
                severity,
                f"field state moved from {baseline.state} to {candidate.state}",
                baseline=baseline.public,
                candidate=candidate.public,
                **common,
            )
        )
    elif after > before:
        out.append(
            finding(
                "STATE_IMPROVED",
                SEVERITY_INFO,
                f"field state moved from {baseline.state} to {candidate.state}",
                baseline=baseline.public,
                candidate=candidate.public,
                **common,
            )
        )
    else:
        out.append(
            finding(
                "STATE_CHANGED",
                SEVERITY_INFO,
                f"field state moved from {baseline.state} to {candidate.state}",
                baseline=baseline.public,
                candidate=candidate.public,
                **common,
            )
        )
    return out


def compare_reports(
    baseline: dict[str, Any] | ReportView,
    candidate: dict[str, Any] | ReportView,
    *,
    clock: Callable[[], str] | None = None,
    allow_mode_mismatch: bool = False,
    allow_contract_mismatch: bool = False,
) -> dict[str, Any]:
    """Compare two registry reports and return the machine-readable result."""
    base = (
        baseline
        if isinstance(baseline, ReportView)
        else ReportView.from_report(baseline, label="baseline")
    )
    cand = (
        candidate
        if isinstance(candidate, ReportView)
        else ReportView.from_report(candidate, label="candidate")
    )
    comparison = RegistryComparison(
        clock=clock,
        allow_mode_mismatch=allow_mode_mismatch,
        allow_contract_mismatch=allow_contract_mismatch,
    )
    return comparison.compare(base, cand)


def _digest_body(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"generated_at", "comparison_sha256"}
    }


# -- outputs ---------------------------------------------------------------
def sanitize_comparison(result: dict[str, Any]) -> dict[str, Any]:
    """A shareable projection with no store paths, object revisions or hashes."""
    return {
        "schema_version": result["schema_version"],
        "baseline": result["baseline"],
        "candidate": result["candidate"],
        "contract": result["contract"],
        "as_of": result["as_of"],
        "thresholds": result["thresholds"],
        "severity_scale": result["severity_scale"],
        "exit_status": result["exit_status"],
        "overall_severity": result["overall_severity"],
        "report_level": {
            "finding_counts": result["report_level"]["finding_counts"],
            "findings": [_public_finding(item) for item in result["report_level"]["findings"]],
        },
        "companies": [
            {
                "ticker": company["ticker"],
                "cik": company["cik"],
                "severity": company["severity"],
                "quality": company["quality"],
                "finding_counts": company["finding_counts"],
                "findings": [_public_finding(item) for item in company["findings"]],
            }
            for company in result["companies"]
        ],
        "finding_counts": result["finding_counts"],
        "findings_total": result["findings_total"],
        "skipped_checks": result["skipped_checks"],
        "interpretation_rules": result["interpretation_rules"],
        "comparison_sha256": result["comparison_sha256"],
    }


def _public_finding(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": item["code"],
        "severity": item["severity"],
        "note": item["note"],
        "ticker": item["ticker"],
        "period_end": item["period_end"],
        "field_id": item["field_id"],
        "baseline": item["baseline"],
        "candidate": item["candidate"],
    }


def render_comparison_markdown(result: dict[str, Any], *, sanitized: bool = False) -> str:
    payload = sanitize_comparison(result) if sanitized else result
    lines: list[str] = []
    add = lines.append
    add(f"# Dataset regression comparison ({payload['schema_version']})")
    add("")
    add(
        f"- Baseline: `{payload['baseline']['label']}` "
        f"({payload['baseline']['detail_level']}, as-of {payload['baseline']['as_of']})"
    )
    add(
        f"- Candidate: `{payload['candidate']['label']}` "
        f"({payload['candidate']['detail_level']}, as-of {payload['candidate']['as_of']})"
    )
    add(f"- Overall severity: `{payload['overall_severity']}`")
    add("- Finding counts: " + json.dumps(payload["finding_counts"], sort_keys=True))
    add(f"- Comparison digest: `{payload['comparison_sha256']}`")
    add("")
    add("## Severity scale")
    add("")
    add("| severity | meaning | CI exit status |")
    add("| --- | --- | --- |")
    for severity in SEVERITY_SCALE:
        add(
            f"| `{severity}` | {payload['severity_scale'][severity]} | "
            f"{payload['exit_status'][severity]} |"
        )
    add("")
    add("## Companies")
    add("")
    add(
        "| ticker | CIK baseline | CIK candidate | severity | baseline overall | "
        "candidate overall |"
    )
    add("| --- | --- | --- | --- | --- | --- |")
    for company in payload["companies"]:
        add(
            f"| {company['ticker']} | {company['cik']['baseline'] or '—'} | "
            f"{company['cik']['candidate'] or '—'} | {company['severity']} | "
            f"{company['quality']['overall']['baseline'] or '—'} | "
            f"{company['quality']['overall']['candidate'] or '—'} |"
        )
    add("")
    for section, items in (("Report level", payload["report_level"]["findings"]),) + tuple(
        (company["ticker"], company["findings"]) for company in payload["companies"]
    ):
        if not items:
            continue
        add(f"### {section}")
        add("")
        add("| severity | code | field | period | note |")
        add("| --- | --- | --- | --- | --- |")
        for item in items:
            add(
                f"| {item['severity']} | `{item['code']}` | {item['field_id'] or '—'} | "
                f"{item['period_end'] or '—'} | {item['note']} |"
            )
        add("")
    if payload["skipped_checks"]:
        add("## Not evaluated")
        add("")
        for reason in payload["skipped_checks"]:
            add(f"- {reason}")
        add("")
    add("## Interpretation rules")
    add("")
    for rule in payload["interpretation_rules"]:
        add(f"- {rule}")
    add("")
    return "\n".join(lines)


def write_comparison(
    result: dict[str, Any],
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    *,
    summary_only: bool = False,
) -> dict[str, str]:
    """Write the private comparison and the sanitized summary pair."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    sanitized = sanitize_comparison(result)
    payload = json.dumps(sanitized, indent=2, sort_keys=True) + "\n"
    markdown = render_comparison_markdown(result, sanitized=True)
    assert_no_private_metadata(
        payload + markdown, allowed_digests=(result.get("comparison_sha256") or "",)
    )
    written: dict[str, str] = {}
    if not summary_only:
        private = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
        private_markdown = render_comparison_markdown(result)
        private_json = target / "comparison.json"
        private_md = target / "comparison.md"
        private_json.write_text(private)
        private_md.write_text(private_markdown)
        written["comparison"] = str(private_json)
        written["comparison_markdown"] = str(private_md)
    summary_json = target / "summary.json"
    summary_md = target / "summary.md"
    summary_json.write_text(payload)
    summary_md.write_text(markdown)
    written["summary"] = str(summary_json)
    written["summary_markdown"] = str(summary_md)
    return written


def load_report(path: Path | str) -> dict[str, Any]:
    """Read a registry report or sanitized summary from disk."""
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise IncomparableReports(f"{path}: payload is not a JSON object")
    return payload
