"""Canonical SEC annual fundamentals contract for the private registry.

The contract names ten reported observations plus one documented derivation. For
every observation it preserves issuer identity, source concept, fiscal period,
reported amount, unit, filing date, availability instant, accession, release and
source-object integrity reference, point-in-time eligibility and validation state.

Two distinctions are kept explicit and are never silently normalized:

* consolidated ``NetIncomeLoss`` is not net income attributable to common
  shareholders, so the substitution is recorded as a transformation; and
* ``LongTermDebt`` is not total interest-bearing debt, and it is not total
  liabilities, so a reported total and a derived sum of parts are separate fields.

Absent values are never manufactured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from zion_terminal.temporal import TemporalError, normalize_legacy_date_only

ANNUAL_MIN_DAYS = 330
ANNUAL_MAX_DAYS = 380
CONTRACT_SCHEMA = "zion-sec-fundamentals-contract-v1"
SOURCE_CONTRACTS = ("sec-companyfacts-v1", "sec-pit-facts-v2")

# Validation states. PASS is the only state that means "usable without caveat".
STATE_PASS = "PASS"
STATE_PASS_DERIVED = "PASS_DERIVED"
STATE_SOURCE_UNVALIDATED = "SOURCE_UNVALIDATED"
STATE_SOURCE_VALIDATED = "SOURCE_VALIDATED"
STATE_SOURCE_PARTIALLY_VALIDATED = "SOURCE_PARTIALLY_VALIDATED"
STATE_CONFLICT = "CONFLICT"
STATE_MISSING = "MISSING_OBSERVATION"
STATE_UNIT_MISMATCH = "UNIT_MISMATCH"
STATE_PERIOD_MISMATCH = "PERIOD_MISMATCH"
STATE_NOT_PIT_ELIGIBLE = "NOT_PIT_ELIGIBLE"
STATE_NOT_CONFORMING = "NOT_CONFORMING"
STATE_BLOCKED = "BLOCKED"

DISCLOSED_STATES = (
    STATE_PASS,
    STATE_PASS_DERIVED,
    STATE_SOURCE_VALIDATED,
    STATE_SOURCE_PARTIALLY_VALIDATED,
    STATE_SOURCE_UNVALIDATED,
    STATE_CONFLICT,
    STATE_MISSING,
    STATE_UNIT_MISMATCH,
    STATE_PERIOD_MISMATCH,
    STATE_NOT_PIT_ELIGIBLE,
    STATE_NOT_CONFORMING,
    STATE_BLOCKED,
)


@dataclass(frozen=True)
class FieldSpec:
    """One contract field: period kind, exact unit, and concept priority."""

    field_id: str
    label: str
    period_kind: str  # "duration" | "instant"
    unit: str
    concepts: tuple[str, ...]
    basis: str
    sign_convention: str
    transformations: tuple[str, ...] = ()
    #: ``(concept, reason_code)`` pairs whose use changes the meaning of the field.
    #: A value found through one of these concepts is never promoted to validated,
    #: even when a second source agrees with the same number.
    basis_changing_concepts: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.period_kind not in {"duration", "instant"}:
            raise ValueError(f"{self.field_id}: unsupported period kind {self.period_kind}")


CONTRACT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        field_id="revenue",
        label="Revenue",
        period_kind="duration",
        unit="USD",
        concepts=(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ),
        basis="Consolidated total revenue for the annual period.",
        sign_convention="reported as positive revenue",
        transformations=(
            "A customer-contract revenue subtotal is not total bank revenue; if only "
            "RevenueFromContractWithCustomer* is available for a bank, record SOURCE_UNVALIDATED.",
        ),
    ),
    FieldSpec(
        field_id="operating_income_ebit",
        label="Operating income (EBIT)",
        period_kind="duration",
        unit="USD",
        concepts=("OperatingIncomeLoss",),
        basis="Reported consolidated operating income/loss. EBIT is not derived from net income.",
        sign_convention="losses are negative as reported",
    ),
    FieldSpec(
        field_id="net_income_common",
        label="Net income attributable to common shareholders",
        period_kind="duration",
        unit="USD",
        concepts=("NetIncomeLossAvailableToCommonStockholdersBasic", "NetIncomeLoss"),
        basis="Income attributable to common shareholders for the annual period.",
        sign_convention="losses are negative as reported",
        transformations=(
            "When only NetIncomeLoss (consolidated) is available the value is labelled "
            "NET_INCOME_TO_COMMON_UNADJUSTED and cannot be treated as common income.",
        ),
        basis_changing_concepts=(("NetIncomeLoss", "NET_INCOME_TO_COMMON_UNADJUSTED"),),
    ),
    FieldSpec(
        field_id="depreciation_amortization",
        label="Depreciation and amortization",
        period_kind="duration",
        unit="USD",
        concepts=(
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization",
        ),
        basis="Cash-flow-statement depreciation, depletion and amortization.",
        sign_convention="reported as a positive non-cash charge",
    ),
    FieldSpec(
        field_id="capital_expenditure",
        label="Capital expenditure",
        period_kind="duration",
        unit="USD",
        concepts=(
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ),
        basis="Cash paid for property, plant and equipment (cash-flow-statement magnitude).",
        sign_convention="reported as a positive cash outflow magnitude, never negated",
    ),
    FieldSpec(
        field_id="diluted_weighted_average_shares",
        label="Diluted weighted-average shares",
        period_kind="duration",
        unit="shares",
        concepts=("WeightedAverageNumberOfDilutedSharesOutstanding",),
        basis="Diluted weighted-average shares outstanding over the annual period.",
        sign_convention="positive share count",
        transformations=(
            "An instant common shares outstanding count is a different measure and does not "
            "satisfy this field.",
        ),
    ),
    FieldSpec(
        field_id="cash_and_equivalents",
        label="Cash and cash equivalents",
        period_kind="instant",
        unit="USD",
        concepts=("CashAndCashEquivalentsAtCarryingValue",),
        basis="Carrying value of cash and cash equivalents at the fiscal period end.",
        sign_convention="positive balance",
    ),
    FieldSpec(
        field_id="debt_current",
        label="Interest-bearing debt, current portion",
        period_kind="instant",
        unit="USD",
        concepts=("LongTermDebtCurrent", "DebtCurrent"),
        basis="Current portion of reported long-term debt at the fiscal period end.",
        sign_convention="positive carrying amount",
    ),
    FieldSpec(
        field_id="debt_short_term_borrowings",
        label="Short-term borrowings and commercial paper",
        period_kind="instant",
        unit="USD",
        concepts=("CommercialPaper", "ShortTermBorrowings"),
        basis="Short-term borrowings outside the long-term debt schedule at the fiscal period end.",
        sign_convention="positive carrying amount",
        transformations=(
            "Kept separate from the long-term debt split because issuers such as AAPL report it "
            "outside LongTermDebt; combined into the short-term total for comparison only.",
        ),
    ),
    FieldSpec(
        field_id="debt_noncurrent",
        label="Interest-bearing debt, noncurrent",
        period_kind="instant",
        unit="USD",
        concepts=("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"),
        basis="Noncurrent interest-bearing debt at the fiscal period end.",
        sign_convention="positive carrying amount",
    ),
    FieldSpec(
        field_id="debt_total_reported",
        label="Reported long-term debt total (cross-check only)",
        period_kind="instant",
        unit="USD",
        concepts=("LongTermDebt",),
        basis="Issuer-reported long-term debt total including its current portion.",
        sign_convention="positive carrying amount",
        transformations=(
            "This is not total liabilities and not operating lease obligations; it is used only "
            "to reconcile the current/noncurrent split.",
        ),
    ),
)

FIELD_IDS = tuple(spec.field_id for spec in CONTRACT_FIELDS)
DERIVED_FIELD = "interest_bearing_debt_total"
DERIVED_PARTS = ("debt_noncurrent", "debt_current")
SHORT_TERM_DERIVED_FIELD = "debt_short_term_total"
SHORT_TERM_DERIVED_PARTS = ("debt_current", "debt_short_term_borrowings")
DERIVED_FIELDS = (DERIVED_FIELD, SHORT_TERM_DERIVED_FIELD)
DURATION_FIELDS = tuple(spec.field_id for spec in CONTRACT_FIELDS if spec.period_kind == "duration")

#: Concepts that are disclosed but do not satisfy a contract field. They exist so a
#: missing field can name the issuer's own alternative tag instead of staying silent.
PROBE_CONCEPTS: dict[str, tuple[str, ...]] = {
    "depreciation_amortization": (
        "Depreciation",
        "AmortizationOfIntangibleAssets",
        "FinanceLeaseRightOfUseAssetAmortization",
        "DepreciationAmortizationAndOther",
    ),
    "revenue": ("RevenueFromContractWithCustomerMember",),
    "diluted_weighted_average_shares": ("WeightedAverageNumberOfSharesOutstandingBasic",),
}

#: Relative and absolute slack when reconciling a reported total with a sum of parts.
#: Issuers round statement subtotals, so a tiny difference is a rounding difference.
TOTAL_RECONCILIATION_RELATIVE_SLACK = Decimal("0.005")
TOTAL_RECONCILIATION_ABSOLUTE_SLACK = Decimal("1000000")

_CONCEPT_INDEX: dict[str, tuple[FieldSpec, str, bool]] = {}
for _spec in CONTRACT_FIELDS:
    for _concept in _spec.concepts:
        _CONCEPT_INDEX.setdefault(_concept, (_spec, _spec.field_id, True))
for _field_id, _concepts in PROBE_CONCEPTS.items():
    _probe_spec = next(spec for spec in CONTRACT_FIELDS if spec.field_id == _field_id)
    for _concept in _concepts:
        _CONCEPT_INDEX.setdefault(_concept, (_probe_spec, _field_id, False))

CONTRACT_CONCEPTS = frozenset(_CONCEPT_INDEX)
_TAXONOMIES = ("us-gaap", "ifrs-full", "dei")


def _dates_between(start: str, end: str) -> int:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    return (end_date - start_date).days


def field_spec(field_id: str) -> FieldSpec:
    for spec in CONTRACT_FIELDS:
        if spec.field_id == field_id:
            return spec
    raise KeyError(field_id)


@dataclass(frozen=True)
class SourceRecord:
    """Integrity reference for the stored object an observation came from."""

    store: str
    path: str
    sha256: str
    etag: str | None = None
    bytes: int | None = None
    member: str | None = None
    member_crc32: str | None = None
    release_id: str | None = None
    schema_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "store": self.store,
                "path": self.path,
                "sha256": self.sha256,
                "etag": self.etag,
                "bytes": self.bytes,
                "member": self.member,
                "member_crc32": self.member_crc32,
                "release_id": self.release_id,
                "schema_version": self.schema_version,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class SourceObservation:
    """One reported fact, before contract-level validation."""

    field_id: str
    concept: str
    taxonomy: str
    unit: str
    value: Decimal
    period_kind: str
    period_start: str | None
    period_end: str
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    frame: str | None
    accession: str
    filed: str
    available_at: str
    availability_evidence: str
    source: SourceRecord
    contract_equivalent: bool = True

    @property
    def duration_days(self) -> int | None:
        if self.period_kind != "duration" or not self.period_start:
            return None
        return _dates_between(self.period_start, self.period_end)

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "concept": self.concept,
            "taxonomy": self.taxonomy,
            "unit": self.unit,
            "value": format(self.value, "f"),
            "period_kind": self.period_kind,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "duration_days": self.duration_days,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "form": self.form,
            "frame": self.frame,
            "accession": self.accession,
            "filed": self.filed,
            "available_at": self.available_at,
            "availability_evidence": self.availability_evidence,
            "contract_equivalent": self.contract_equivalent,
            "source": self.source.as_dict(),
        }


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _availability(filed: Any) -> tuple[str, str]:
    """Availability instant from filing evidence.

    CompanyFacts carries a date-only ``filed`` value, so the documented
    DATE_ONLY_NEXT_DAY_ET_V1 policy applies and the evidence is marked DATE_ONLY.
    An explicit acceptance timestamp, when present, is used unchanged.
    """
    if isinstance(filed, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", filed):
        return normalize_legacy_date_only(filed).iso_utc, "DATE_ONLY"
    if isinstance(filed, str):
        try:
            return normalize_legacy_date_only(filed).iso_utc, "DATE_ONLY"
        except TemporalError:
            return filed, "EXACT_ACCEPTED_AT"
    return "", "UNKNOWN"


def parse_companyfacts(payload: Any, *, source: SourceRecord) -> list[SourceObservation]:
    """Extract contract observations from a SEC CompanyFacts payload.

    Only concepts named by the contract are materialized, so parsing a multi-
    megabyte payload never builds a general fact index.
    """
    if not isinstance(payload, dict):
        raise ValueError("companyfacts payload must be an object")
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        raise ValueError("companyfacts payload has no facts block")
    observations: list[SourceObservation] = []
    for taxonomy in _TAXONOMIES:
        block = facts.get(taxonomy)
        if not isinstance(block, dict):
            continue
        for concept, entry in block.items():
            indexed = _CONCEPT_INDEX.get(concept)
            if indexed is None or not isinstance(entry, dict):
                continue
            _spec, field_id, equivalent = indexed
            units = entry.get("units")
            if not isinstance(units, dict):
                continue
            for unit, rows in units.items():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    value = _decimal(row.get("val"))
                    end = row.get("end")
                    if value is None or not isinstance(end, str):
                        continue
                    start = row.get("start") if isinstance(row.get("start"), str) else None
                    period_kind = "duration" if start else "instant"
                    available_at, evidence = _availability(row.get("filed"))
                    observations.append(
                        SourceObservation(
                            field_id=field_id,
                            concept=concept,
                            taxonomy=taxonomy,
                            unit=unit,
                            value=value,
                            period_kind=period_kind,
                            period_start=start,
                            period_end=end,
                            fiscal_year=row.get("fy") if isinstance(row.get("fy"), int) else None,
                            fiscal_period=row.get("fp") if isinstance(row.get("fp"), str) else None,
                            form=row.get("form") if isinstance(row.get("form"), str) else None,
                            frame=row.get("frame") if isinstance(row.get("frame"), str) else None,
                            accession=str(row.get("accn") or ""),
                            filed=str(row.get("filed") or ""),
                            available_at=available_at,
                            availability_evidence=evidence,
                            source=source,
                            contract_equivalent=equivalent,
                        )
                    )
    return observations


def annual_periods(observations: Sequence[SourceObservation], *, limit: int = 3) -> list[str]:
    """Most recent eligible annual period ends, newest first.

    An annual period is a duration observation of the contract's revenue or
    operating-income family whose length is 330-380 days, which excludes
    year-to-date and stub periods without inventing a fiscal calendar.
    """
    ends: set[str] = set()
    for observation in observations:
        if observation.period_kind != "duration" or observation.field_id not in {
            "revenue",
            "operating_income_ebit",
            "net_income_common",
        }:
            continue
        days = observation.duration_days
        if days is None or not ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS:
            continue
        ends.add(observation.period_end)
    return sorted(ends, reverse=True)[:limit]


@dataclass
class FieldResult:
    field_id: str
    label: str
    period_end: str
    state: str
    value: Decimal | None = None
    unit: str | None = None
    concept: str | None = None
    basis: str = ""
    reason_codes: list[str] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)
    observation: SourceObservation | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    derivations: list[dict[str, Any]] = field(default_factory=list)
    cross_sources: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "label": self.label,
            "period_end": self.period_end,
            "state": self.state,
            "value": None if self.value is None else format(self.value, "f"),
            "unit": self.unit,
            "concept": self.concept,
            "basis": self.basis,
            "reason_codes": list(self.reason_codes),
            "transformations": list(self.transformations),
            "observation": None if self.observation is None else self.observation.as_dict(),
            "candidates": list(self.candidates),
            "derivations": list(self.derivations),
            "cross_sources": list(self.cross_sources),
        }


def _equal(left: Decimal, right: Decimal, *, tolerance: Decimal = Decimal("0")) -> bool:
    return abs(left - right) <= tolerance


def _select_winner(
    rows: Sequence[SourceObservation], spec: FieldSpec
) -> tuple[SourceObservation, list[SourceObservation]]:
    """Highest-priority concept; within a concept the latest filing wins.

    Returns the winner and the rows it superseded.
    """
    for concept in spec.concepts:
        matching = [row for row in rows if row.concept == concept]
        if not matching:
            continue
        latest = max(matching, key=lambda row: (row.filed, row.accession))
        superseded = [row for row in matching if row is not latest]
        return latest, superseded
    raise LookupError("no candidates")


def evaluate_field(
    spec: FieldSpec,
    observations: Sequence[SourceObservation],
    *,
    period_end: str,
    as_of: str | None,
) -> FieldResult:
    """Evaluate one contract field for one fiscal period end."""
    result = FieldResult(
        field_id=spec.field_id,
        label=spec.label,
        period_end=period_end,
        state=STATE_MISSING,
        unit=spec.unit,
        basis=spec.basis,
        transformations=list(spec.transformations),
    )
    relevant: list[SourceObservation] = []
    alternatives: list[dict[str, Any]] = []
    _ALTERNATIVE_SEEN: dict[tuple[str, str, str], int] = {}
    off_period: set[str] = set()
    for observation in observations:
        if observation.field_id != spec.field_id:
            continue
        if observation.period_end != period_end:
            continue
        if not observation.contract_equivalent:
            key = (observation.concept, observation.unit, format(observation.value, "f"))
            prior = _ALTERNATIVE_SEEN.get(key)
            entry = {
                "kind": "alternative_tag",
                "concept": observation.concept,
                "value": format(observation.value, "f"),
                "unit": observation.unit,
                "accession": observation.accession,
                "filed": observation.filed,
                "note": "issuer tag present for this period; it does not satisfy "
                "the contract field",
            }
            if prior is None:
                alternatives.append(entry)
                _ALTERNATIVE_SEEN[key] = len(alternatives) - 1
            elif observation.filed > alternatives[prior]["filed"]:
                # The same disclosure repeated as a prior-year comparative in later
                # filings: keep the most recent filing as the disclosure evidence.
                alternatives[prior] = entry
            continue
        if observation.period_kind != spec.period_kind:
            off_period.add(f"PERIOD_KIND:{observation.period_kind}")
            continue
        if spec.period_kind == "duration":
            days = observation.duration_days
            if days is None or not ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS:
                off_period.add(f"DURATION_DAYS:{days}")
                continue
        relevant.append(observation)

    if not relevant:
        if off_period:
            result.state = STATE_PERIOD_MISMATCH
            result.reason_codes.append("SOURCE_ROW_NOT_ALIGNED_TO_ANNUAL_PERIOD")
            result.reason_codes.extend(sorted(off_period))
        else:
            result.reason_codes.append("NO_SOURCE_ROW_FOR_PERIOD")
        if alternatives:
            result.reason_codes.append("ALTERNATIVE_TAG_NOT_CONTRACT_EQUIVALENT")
            result.derivations.extend(alternatives)
        return result

    right_unit = [row for row in relevant if row.unit == spec.unit]
    wrong_unit = [row for row in relevant if row.unit != spec.unit]
    result.candidates = [
        {
            "concept": row.concept,
            "unit": row.unit,
            "value": format(row.value, "f"),
            "accession": row.accession,
            "filed": row.filed,
            "eligible": as_of is None or row.available_at <= as_of,
        }
        for row in relevant
    ]

    if not right_unit:
        result.state = STATE_UNIT_MISMATCH
        result.reason_codes.append("UNIT_NOT_IN_CONTRACT")
        result.reason_codes.extend(sorted({f"OBSERVED_UNIT:{row.unit}" for row in wrong_unit}))
        return result

    if as_of is not None:
        eligible = [row for row in right_unit if row.available_at <= as_of]
        if not eligible:
            result.state = STATE_NOT_PIT_ELIGIBLE
            result.reason_codes.append("AVAILABLE_AFTER_AS_OF")
            result.candidates = [row for row in result.candidates if not row["eligible"]]
            return result
    else:
        eligible = list(right_unit)

    winner, superseded = _select_winner(eligible, spec)
    result.observation = winner
    result.value = winner.value
    result.concept = winner.concept
    result.unit = winner.unit
    if superseded:
        result.reason_codes.append("SUPERSEDED_FILINGS_IGNORED")
        result.derivations.extend(
            {
                "kind": "superseded_observation",
                "concept": row.concept,
                "accession": row.accession,
                "filed": row.filed,
                "value": format(row.value, "f"),
            }
            for row in superseded
        )
    if alternatives:
        result.reason_codes.append("ALTERNATIVE_TAG_NOT_CONTRACT_EQUIVALENT")
        result.derivations.extend(alternatives)

    conflicts = []
    for row in eligible:
        if row.concept == winner.concept or row is winner:
            continue
        if spec.concepts.index(row.concept) < spec.concepts.index(winner.concept):
            continue
        lower = [other for other in eligible if other.concept == row.concept]
        chosen = max(lower, key=lambda item: (item.filed, item.accession))
        if not _equal(chosen.value, winner.value):
            conflicts.append(
                {
                    "kind": "concept_disagreement",
                    "concept": chosen.concept,
                    "value": format(chosen.value, "f"),
                    "accession": chosen.accession,
                    "filed": chosen.filed,
                }
            )
    if conflicts:
        result.state = STATE_CONFLICT
        result.reason_codes.append("CONFLICTING_SOURCE_CONCEPTS")
        result.derivations.extend(conflicts)
        return result

    for concept, reason_code in spec.basis_changing_concepts:
        if winner.concept == concept:
            result.state = STATE_SOURCE_UNVALIDATED
            result.reason_codes.append(reason_code)
            return result

    result.state = STATE_SOURCE_UNVALIDATED
    result.reason_codes.append("SINGLE_SOURCE_OBSERVATION")
    return result


def _derive_sum(
    field_id: str,
    label: str,
    part_ids: tuple[str, ...],
    parts: dict[str, FieldResult],
    *,
    period_end: str,
    basis: str,
    transformations: Sequence[str],
    reported: FieldResult | None = None,
) -> FieldResult:
    """Sum recorded parts, optionally reconciling against a reported total."""
    result = FieldResult(
        field_id=field_id,
        label=label,
        period_end=period_end,
        state=STATE_MISSING,
        unit="USD",
        basis=basis,
        transformations=list(transformations),
    )
    missing = [part for part in part_ids if parts.get(part) is None or parts[part].value is None]
    if missing:
        result.state = STATE_MISSING
        result.reason_codes.extend(f"MISSING_PART:{part}" for part in missing)
        return result
    result.value = sum((parts[part].value for part in part_ids), Decimal(0))
    result.concept = "+".join(parts[part].concept or part for part in part_ids)
    result.derivations = [
        {
            "kind": "derived_component",
            "field_id": part,
            "concept": parts[part].concept,
            "value": format(parts[part].value or Decimal(0), "f"),
            "accession": parts[part].observation.accession if parts[part].observation else None,
        }
        for part in part_ids
    ]
    promoted = {STATE_PASS, STATE_PASS_DERIVED, STATE_SOURCE_VALIDATED, STATE_SOURCE_UNVALIDATED}
    states = {parts[part].state for part in part_ids}
    if states - promoted:
        result.state = STATE_SOURCE_UNVALIDATED
        result.reason_codes.append("COMPONENT_NOT_PASS")
    else:
        result.state = STATE_PASS_DERIVED
    if reported is not None and reported.value is not None:
        delta = reported.value - result.value
        slack = max(
            TOTAL_RECONCILIATION_ABSOLUTE_SLACK,
            abs(reported.value) * TOTAL_RECONCILIATION_RELATIVE_SLACK,
        )
        within = abs(delta) <= slack
        result.derivations.append(
            {
                "kind": "reported_total",
                "concept": reported.concept,
                "value": format(reported.value, "f"),
                "accession": reported.observation.accession if reported.observation else None,
                "delta": format(delta, "f"),
                "within_rounding_slack": within,
            }
        )
        if within and delta != 0:
            result.reason_codes.append("ROUNDING_DIFFERENCE_WITHIN_SLACK")
        elif not within:
            result.state = STATE_CONFLICT
            result.reason_codes.append("REPORTED_TOTAL_DISAGREES_WITH_DERIVED_SUM")
    return result


def derive_interest_bearing_debt(
    parts: dict[str, FieldResult],
    *,
    period_end: str,
    reported: FieldResult | None = None,
) -> FieldResult:
    """Sum the long-term debt current and noncurrent parts.

    The sum is only produced when every part is available; the reported
    ``LongTermDebt`` total is reconciled, never substituted, and a disagreement
    beyond rounding slack is a conflict. Short-term borrowings outside the
    long-term debt schedule are reported separately in the short-term total.
    """
    return _derive_sum(
        DERIVED_FIELD,
        "Long-term debt, current plus noncurrent (sum of reported parts)",
        DERIVED_PARTS,
        parts,
        period_end=period_end,
        basis="Current portion plus noncurrent long-term debt as separately reported.",
        transformations=[
            "Derived, not reported. It excludes short-term borrowings, operating leases "
            "and total liabilities.",
        ],
        reported=reported,
    )


def derive_short_term_debt(
    parts: dict[str, FieldResult],
    *,
    period_end: str,
) -> FieldResult:
    """Sum the current long-term portion with short-term borrowings.

    This is the comparable basis for a packet or serving layer that reports a single
    short-term debt number.
    """
    return _derive_sum(
        SHORT_TERM_DERIVED_FIELD,
        "Short-term interest-bearing debt (current portion plus short-term borrowings)",
        SHORT_TERM_DERIVED_PARTS,
        parts,
        period_end=period_end,
        basis="Comparable basis for a single reported short-term debt figure.",
        transformations=[
            "Derived, not reported. Used only to compare with an externally published "
            "short-term debt figure.",
        ],
    )


@dataclass
class CompanyContractResult:
    """Per-company contract evaluation across the requested annual periods."""

    ticker: str
    cik: str | None
    as_of: str | None
    periods: list[str] = field(default_factory=list)
    fields: dict[str, dict[str, FieldResult]] = field(default_factory=dict)
    observations_examined: int = 0
    source: SourceRecord | None = None
    limitations: list[str] = field(default_factory=list)

    def field_states(self) -> dict[str, dict[str, str]]:
        return {
            period: {field_id: result.state for field_id, result in fields.items()}
            for period, fields in ((period, self.fields.get(period, {})) for period in self.periods)
        }

    def missing_fields(self) -> dict[str, list[str]]:
        return {
            period: sorted(
                field_id
                for field_id, result in self.fields.get(period, {}).items()
                if result.state == STATE_MISSING
            )
            for period in self.periods
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "cik": self.cik,
            "as_of": self.as_of,
            "contract": CONTRACT_SCHEMA,
            "periods": list(self.periods),
            "observations_examined": self.observations_examined,
            "source": None if self.source is None else self.source.as_dict(),
            "field_states": self.field_states(),
            "missing_fields": self.missing_fields(),
            "fields": {
                period: {field_id: result.as_dict() for field_id, result in fields.items()}
                for period, fields in self.fields.items()
            },
            "limitations": list(self.limitations),
        }


def evaluate_company(
    observations: Sequence[SourceObservation],
    *,
    ticker: str,
    cik: str | None,
    as_of: str | None = None,
    periods: int = 3,
    source: SourceRecord | None = None,
) -> CompanyContractResult:
    """Run the contract over the most recent eligible annual periods."""
    result = CompanyContractResult(
        ticker=ticker.upper(),
        cik=cik,
        as_of=as_of,
        observations_examined=len(observations),
        source=source,
    )
    eligible_all = [row for row in observations if as_of is None or row.available_at <= as_of]
    result.periods = annual_periods(eligible_all, limit=periods)
    if not result.periods:
        result.periods = annual_periods(observations, limit=periods)
        if result.periods:
            result.limitations.append("PERIODS_EXIST_ONLY_AFTER_AS_OF")
    for period_end in result.periods:
        fields: dict[str, FieldResult] = {}
        for spec in CONTRACT_FIELDS:
            fields[spec.field_id] = evaluate_field(
                spec, observations, period_end=period_end, as_of=as_of
            )
        fields[DERIVED_FIELD] = derive_interest_bearing_debt(
            fields, period_end=period_end, reported=fields.get("debt_total_reported")
        )
        fields[SHORT_TERM_DERIVED_FIELD] = derive_short_term_debt(fields, period_end=period_end)
        result.fields[period_end] = fields
    return result


def cross_validate(
    result: CompanyContractResult,
    secondary: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Confirm contract values against a second independent source object.

    ``secondary`` entries describe a value from another source object:
    ``{field_id, period_end, value, unit, source, basis, comparable}``. A match
    promotes the field to SOURCE_VALIDATED; a mismatch is a conflict; a value that
    measures a different basis is recorded as NOT_COMPARABLE rather than coerced.
    """
    findings: list[dict[str, Any]] = []
    for entry in secondary:
        field_id = entry.get("field_id")
        period_end = entry.get("period_end")
        target = result.fields.get(period_end, {}).get(field_id or "")
        comparison = {
            "field_id": field_id,
            "period_end": period_end,
            "secondary_source": entry.get("source"),
            "secondary_value": None
            if entry.get("value") is None
            else format(Decimal(str(entry["value"])), "f"),
            "secondary_unit": entry.get("unit"),
            "basis_note": entry.get("basis"),
        }
        if target is None:
            comparison["state"] = "PERIOD_NOT_EVALUATED"
            findings.append(comparison)
            continue
        if not entry.get("comparable", True):
            comparison["state"] = "NOT_COMPARABLE"
            findings.append(comparison)
            continue
        if target.value is None:
            comparison["state"] = "PRIMARY_NOT_AVAILABLE"
            findings.append(comparison)
            continue
        secondary_value = Decimal(str(entry["value"]))
        if target.unit != entry.get("unit"):
            comparison["state"] = "UNIT_MISMATCH"
            findings.append(comparison)
            if target.state in {STATE_SOURCE_UNVALIDATED, STATE_SOURCE_VALIDATED}:
                target.state = STATE_SOURCE_UNVALIDATED
            continue
        if _equal(target.value, secondary_value):
            comparison["state"] = "AGREES"
            if (
                target.state == STATE_PASS_DERIVED
                and "SECOND_SOURCE_AGREES" not in target.reason_codes
            ):
                target.reason_codes.append("SECOND_SOURCE_AGREES")
            elif target.state in {STATE_SOURCE_UNVALIDATED, STATE_PASS}:
                spec = field_spec(field_id) if field_id in FIELD_IDS else None
                basis_changing = spec is not None and any(
                    concept == target.concept for concept, _ in spec.basis_changing_concepts
                )
                if not basis_changing:
                    target.state = STATE_SOURCE_VALIDATED
                    if "SECOND_SOURCE_AGREES" not in target.reason_codes:
                        target.reason_codes.append("SECOND_SOURCE_AGREES")
                else:
                    # A non-preferred source concept stays unvalidated for the contract
                    # field even when a second source agrees with it: agreeing on the
                    # wrong measure is not validation. Derived fields are compared as a
                    # whole, so their concept string is a sum, not a single tag.
                    target.reason_codes.append("SECOND_SOURCE_AGREES_ON_BASIS_CHANGING_CONCEPT")
        else:
            comparison["state"] = "CONFLICT"
            comparison["delta"] = format(target.value - secondary_value, "f")
            target.state = STATE_CONFLICT
            target.reason_codes.append("SECOND_SOURCE_DISAGREES")
        target.cross_sources.append(comparison)
        findings.append(comparison)
    return findings


def frame_report_hash(report: dict[str, Any]) -> str:
    """Stable digest of a machine-readable report body."""
    import hashlib
    import json

    body = json.dumps(report, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()
