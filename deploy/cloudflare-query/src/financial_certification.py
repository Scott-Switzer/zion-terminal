"""Metric/period evidence labels for public SEC facts, never blanket certification."""

import re
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from sec_engine import REGISTRY
from temporal_core import parse_instant

VERSION = "sec-financial-certification-v1"
RANK = (
    "SOURCE_OBSERVED",
    "INDEPENDENTLY_RECONCILED",
    "DERIVATION_VERIFIED",
    "ACCOUNTING_CERTIFIED",
)
LINEAR_IDENTITIES = {
    "REVENUE_MINUS_COST_EQUALS_GROSS_PROFIT": (
        "revenue",
        {"revenue": 1, "cost_of_revenue": -1, "gross_profit": -1},
    ),
    "GROSS_PROFIT_MINUS_OPEX_EQUALS_OPERATING_INCOME": (
        "operating_income",
        {"gross_profit": 1, "operating_expenses": -1, "operating_income": -1},
    ),
    "PRETAX_MINUS_TAX_EQUALS_NET_INCOME": (
        "net_income",
        {"pretax_income": 1, "tax_expense": -1, "net_income": -1},
    ),
}


def exact(value):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("EXACT_DECIMAL_STRING_REQUIRED")
    try:
        number = Decimal(value)
        if (
            not number.is_finite()
            or abs(number.adjusted()) > 80
            or abs(number.as_tuple().exponent) > 80
        ):
            raise ValueError("EXACT_DECIMAL_BUDGET")
        return Fraction(number)
    except (InvalidOperation, OverflowError):
        raise ValueError("EXACT_DECIMAL_STRING_REQUIRED") from None


def scope(row):
    return tuple(row.get(k) for k in ("period_start", "period_end", "unit", "currency"))


def source_evidence(rows):
    return [
        {
            key: row.get(value)
            for key, value in {
                "source_id": "source_observation_id",
                "concept": "concept",
                "accession": "accession",
                "form": "form",
                "available_at": "available_at",
                "unit": "unit",
                "currency": "currency",
                "period_start": "period_start",
                "period_end": "period_end",
                "member_sha256": "member_sha256",
            }.items()
        }
        for row in rows
    ]


def component_terms(component, lookup, depth=0):
    if depth > 8:
        raise ValueError("DERIVATION_COMPONENT_DEPTH")
    ids = component.get("source_observation_ids", [])
    if not ids or len(ids) != len(set(ids)) or any(sid not in lookup for sid in ids):
        raise ValueError("DERIVATION_COMPONENT_SOURCE_BINDING_MISSING")
    leaves = [lookup[sid] for sid in ids]
    if any(
        leaf.get("unit") != component.get("unit")
        or leaf.get("currency") != component.get("currency")
        for leaf in leaves
    ):
        raise ValueError("DERIVATION_COMPONENT_UNIT_MISMATCH")
    nested = component.get("source_components", [])
    if nested:
        terms = {}
        for index, part in enumerate(nested):
            sign = 1 if component.get("formula") == "SUM" or index == 0 else -1
            for sid, coefficient in component_terms(part, lookup, depth + 1).items():
                terms[sid] = terms.get(sid, 0) + sign * coefficient
        if set(terms) != set(ids):
            raise ValueError("DERIVATION_COMPONENT_SOURCE_BINDING_MISMATCH")
        validation = {
            **component,
            "source_terms": [
                {"source_observation_id": sid, "coefficient": coefficient}
                for sid, coefficient in terms.items()
            ],
        }
        validate_derivation_definition(validation, leaves)
    elif len(leaves) == 1:
        if leaves[0].get("metric") != component.get("metric") or scope(leaves[0]) != scope(
            component
        ):
            raise ValueError("DERIVATION_COMPONENT_PERIOD_OR_ROLE_MISMATCH")
        terms = {ids[0]: 1}
    else:
        if any(leaf.get("metric") != component.get("metric") for leaf in leaves):
            raise ValueError("DERIVATION_COMPONENT_ROLE_MISMATCH")
        ordered = sorted(leaves, key=lambda row: row["period_end"])
        if component.get("formula") == "SUBTRACT" and len(ordered) == 2:
            prior, latest = ordered
            if (
                latest["period_start"] != prior["period_start"]
                or latest["period_end"] != component["period_end"]
                or (date.fromisoformat(prior["period_end"]) + timedelta(days=1)).isoformat()
                != component["period_start"]
            ):
                raise ValueError("DERIVATION_COMPONENT_PERIOD_MISMATCH")
            terms = {latest["source_observation_id"]: 1, prior["source_observation_id"]: -1}
        elif component.get("formula") == "SUM":
            if (
                ordered[0]["period_start"] != component["period_start"]
                or ordered[-1]["period_end"] != component["period_end"]
                or any(
                    (date.fromisoformat(left["period_end"]) + timedelta(days=1)).isoformat()
                    != right["period_start"]
                    for left, right in zip(ordered, ordered[1:])
                )
            ):
                raise ValueError("DERIVATION_COMPONENT_PERIOD_MISMATCH")
            terms = {leaf["source_observation_id"]: 1 for leaf in leaves}
        else:
            raise ValueError("DERIVATION_COMPONENT_DEFINITION_UNSUPPORTED")
    if sum(
        (exact(lookup[sid]["value_decimal"]) * coefficient for sid, coefficient in terms.items()),
        Fraction(),
    ) != exact(component["value_decimal"]):
        raise ValueError("DERIVATION_COMPONENT_VALUE_MISMATCH")
    return terms


def validate_derivation_definition(row, leaves):
    components = row.get("source_components", [])
    metric = row["metric"]
    if components:
        lookup = {leaf["source_observation_id"]: leaf for leaf in leaves}
        expected_terms = {}
        for index, component in enumerate(components):
            sign = 1 if row.get("formula") == "SUM" or index == 0 else -1
            for sid, coefficient in component_terms(component, lookup).items():
                expected_terms[sid] = expected_terms.get(sid, 0) + sign * coefficient
        expected_terms = {sid: c for sid, c in expected_terms.items() if c}
        if expected_terms != {
            term["source_observation_id"]: term["coefficient"] for term in row["source_terms"]
        }:
            raise ValueError("DERIVATION_COMPONENT_TERMS_MISMATCH")
    if metric == "free_cash_flow":
        roles = {"operating_cash_flow", "capital_expenditures"}
        if {leaf["metric"] for leaf in leaves} != roles or row.get("formula") != "SUBTRACT":
            raise ValueError("FREE_CASH_FLOW_DEFINITION_MISMATCH")
        if components:
            by_metric = {part["metric"]: part for part in components}
            if (
                len(components) != 2
                or set(by_metric) != roles
                or any(scope(part) != scope(row) for part in components)
            ):
                raise ValueError("FREE_CASH_FLOW_COMPONENT_SCOPE_MISMATCH")
            expected = exact(by_metric["operating_cash_flow"]["value_decimal"]) - exact(
                by_metric["capital_expenditures"]["value_decimal"]
            )
            if (
                expected != exact(row["value_decimal"])
                or exact(by_metric["capital_expenditures"]["value_decimal"]) < 0
            ):
                raise ValueError("FREE_CASH_FLOW_DEFINITION_MISMATCH")
        else:
            if any(scope(leaf) != scope(row) for leaf in leaves) or any(
                term["coefficient"]
                != (
                    1
                    if next(
                        leaf
                        for leaf in leaves
                        if leaf["source_observation_id"] == term["source_observation_id"]
                    )["metric"]
                    == "operating_cash_flow"
                    else -1
                )
                for term in row["source_terms"]
            ):
                raise ValueError("FREE_CASH_FLOW_DEFINITION_MISMATCH")
    elif metric == "total_debt":
        if row.get("formula") != "SUM" or any(
            scope(leaf) != scope(row) or not leaf["metric"].startswith("debt_component:")
            for leaf in leaves
        ):
            raise ValueError("DEBT_COMPONENT_SCOPE_OR_DEFINITION_MISMATCH")
    else:
        if not components or any(
            part.get("metric") != metric
            or part.get("unit") != row["unit"]
            or part.get("currency") != row.get("currency")
            for part in components
        ):
            raise ValueError("PERIOD_DERIVATION_COMPONENT_SCOPE_REQUIRED")
        if row["formula"] == "SUM":
            ordered = sorted(components, key=lambda part: part["period_start"])
            if (
                ordered[0]["period_start"] != row["period_start"]
                or ordered[-1]["period_end"] != row["period_end"]
                or any(
                    (date.fromisoformat(left["period_end"]) + timedelta(days=1)).isoformat()
                    != right["period_start"]
                    for left, right in zip(ordered, ordered[1:])
                )
            ):
                raise ValueError("PERIOD_DERIVATION_OVERLAP_OR_GAP")
        elif (
            len(components) != 2
            or components[0]["period_start"] != components[1]["period_start"]
            or components[0]["period_end"] != row["period_end"]
            or (date.fromisoformat(components[1]["period_end"]) + timedelta(days=1)).isoformat()
            != row["period_start"]
        ):
            raise ValueError("PERIOD_DERIVATION_SCOPE_MISMATCH")


def validate_sources(data, row, lookup, cutoff):
    metric = row.get("metric")
    if metric in data.get("policy", {}).get("quarantined_metrics", {}):
        raise ValueError(data["policy"]["quarantined_metrics"][metric])
    if metric not in REGISTRY or row.get("unit") != REGISTRY[metric]["unit"]:
        raise ValueError("METRIC_DEFINITION_OR_UNIT_UNSUPPORTED")
    end = date.fromisoformat(row["period_end"])
    start = date.fromisoformat(row["period_start"]) if row.get("period_start") else None
    if (start and start > end) or (REGISTRY[metric]["kind"] in ("flow", "weighted") and not start):
        raise ValueError("INVALID_REPORT_PERIOD")
    value = exact(row.get("value_decimal"))
    ids = row.get("source_observation_ids", [])
    if not ids or len(ids) != len(set(ids)) or any(sid not in lookup for sid in ids):
        raise ValueError("SOURCE_LINEAGE_MISSING_OR_DUPLICATED")
    leaves = [lookup[sid] for sid in ids]
    for leaf in leaves:
        if (
            leaf.get("cik") != data["cik"]
            or leaf.get("member_sha256") != data["member_sha256"]
            or not leaf.get("accession")
            or leaf.get("form") not in ("10-K", "10-K/A", "10-Q", "10-Q/A")
        ):
            raise ValueError("SOURCE_IDENTITY_OR_FILING_CONTEXT_INVALID")
        if parse_instant(leaf["available_at"]).epoch_ns > cutoff:
            raise ValueError("SOURCE_NOT_AVAILABLE_AT_CUTOFF")
        if leaf.get("unit") != row["unit"] or leaf.get("currency") != row.get("currency"):
            raise ValueError("SOURCE_UNIT_OR_CURRENCY_MISMATCH")
        exact(leaf.get("value_decimal"))
        native_metric = leaf.get("metric")
        if native_metric in REGISTRY:
            concepts = (
                data.get("policy", {}).get("revenue_concepts", REGISTRY[native_metric]["concepts"])
                if native_metric == "revenue"
                else REGISTRY[native_metric]["concepts"]
            )
            if leaf.get("concept", "").removeprefix("us-gaap:") not in concepts:
                raise ValueError("SOURCE_CONCEPT_DEFINITION_MISMATCH")
        peers = [
            other
            for other in data["observations"]
            if other.get("concept") == leaf.get("concept")
            and scope(other) == scope(leaf)
            and parse_instant(other["available_at"]).epoch_ns <= cutoff
        ]
        latest = max(parse_instant(other["available_at"]).epoch_ns for other in peers)
        versions = [
            other for other in peers if parse_instant(other["available_at"]).epoch_ns == latest
        ]
        if len({other.get("value_decimal") for other in versions}) != 1:
            raise ValueError("CONFLICTING_LATEST_RESTATEMENT_VALUES")
        if parse_instant(leaf["available_at"]).epoch_ns != latest:
            raise ValueError("NONLATEST_SOURCE_VINTAGE")
        if any(
            rejected.get("metric") == leaf.get("metric")
            and rejected.get("concept") == leaf.get("concept")
            and rejected.get("period_start") == leaf.get("period_start")
            and rejected.get("period_end") == leaf.get("period_end")
            and latest <= parse_instant(rejected["available_at"]).epoch_ns <= cutoff
            for rejected in data.get("rejected_contexts", [])
        ):
            raise ValueError("LATEST_SOURCE_CONTEXT_REJECTED")
    derived = row.get("record_kind") == "DERIVED"
    if derived:
        terms = row.get("source_terms", [])
        if (
            row.get("formula") not in ("SUM", "SUBTRACT")
            or not terms
            or {term.get("source_observation_id") for term in terms} != set(ids)
            or len(terms) != len({term.get("source_observation_id") for term in terms})
            or any(
                type(term.get("coefficient")) is not int or abs(term["coefficient"]) > 100
                for term in terms
            )
        ):
            raise ValueError("DERIVATION_DEFINITION_OR_TERMS_UNSUPPORTED")
        calculated = sum(
            (
                exact(lookup[term["source_observation_id"]]["value_decimal"]) * term["coefficient"]
                for term in terms
            ),
            Fraction(),
        )
        if calculated != value:
            raise ValueError("DERIVATION_VALUE_MISMATCH")
        validate_derivation_definition(row, leaves)
        if any(leaf["period_end"] > row["period_end"] for leaf in leaves):
            raise ValueError("DERIVATION_SOURCE_PERIOD_AFTER_RESULT")
    elif (
        len(leaves) != 1
        or leaves[0]["metric"] != metric
        or scope(leaves[0]) != scope(row)
        or exact(leaves[0]["value_decimal"]) != value
    ):
        raise ValueError("REPORTED_SOURCE_VALUE_OR_SCOPE_MISMATCH")
    return leaves, derived


def reconciled(data, row, evidence):
    if not isinstance(evidence, dict) or not evidence.get("method"):
        return False
    if (
        evidence.get("member_sha256") != data.get("member_sha256")
        or evidence.get("registry_sha256") != data.get("registry_sha256")
        or not re.fullmatch(r"[a-f0-9]{64}", str(evidence.get("implementation_sha256", "")))
    ):
        return False
    for checked in evidence.get("checks", []):
        if (
            checked.get("status") == "PASS"
            and all(
                checked.get(key) == row.get(key)
                for key in (
                    "metric",
                    "frequency",
                    "period_start",
                    "period_end",
                    "unit",
                    "value_decimal",
                )
            )
            and set(checked.get("source_ids", [])) == set(row["source_observation_ids"])
        ):
            return True
    return False


def accounting_identity(data, row, item, lookup, cutoff):
    """Only explicit same-filing metric identities may earn accounting status."""
    scope_keys = (
        ("period_end",)
        if item.get("identity_id") == "CASH_MOVEMENT" and row["metric"] == "cash"
        else ("period_start", "period_end")
    )
    if item.get("metric") != row["metric"] or any(item.get(k) != row.get(k) for k in scope_keys):
        return False, None
    identity = item.get("identity_id")
    ids = item.get("component_ids", {})
    if (
        not ids
        or len(set(ids.values())) != len(ids)
        or any(sid not in lookup for sid in ids.values())
    ):
        return False, "ACCOUNTING_SOURCE_INPUTS_MISSING_OR_DUPLICATED"
    parts = {role: lookup[sid] for role, sid in ids.items()}
    leaves = list(parts.values())
    filing = {
        (
            r.get("accession"),
            r.get("form"),
            r.get("available_at"),
            r.get("cik"),
            r.get("member_sha256"),
        )
        for r in leaves
    }
    if (
        len(filing) != 1
        or any(not r.get("accession") or not r.get("available_at") for r in leaves)
        or any(
            r.get("cik") != data["cik"]
            or r.get("member_sha256") != data["member_sha256"]
            or parse_instant(r["available_at"]).epoch_ns > cutoff
            for r in leaves
        )
        or not set(row["source_observation_ids"]) <= set(ids.values())
    ):
        return False, "ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH"
    for part in leaves:
        alternatives = [
            candidate
            for candidate in data["observations"]
            if candidate.get("metric") == part.get("metric")
            and scope(candidate) == scope(part)
            and all(
                candidate.get(key) == part.get(key) for key in ("accession", "form", "available_at")
            )
        ]
        if len({candidate.get("value_decimal") for candidate in alternatives}) > 1:
            return False, "CONFLICTING_ACCOUNTING_COMPONENT_ALTERNATIVES"
    values = {role: exact(part["value_decimal"]) for role, part in parts.items()}
    expected_period = (item.get("period_start"), item.get("period_end"))
    if identity != "CASH_MOVEMENT" and any(
        (p.get("period_start"), p.get("period_end")) != expected_period for p in leaves
    ):
        return False, "ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH"
    if identity in LINEAR_IDENTITIES:
        target, terms = LINEAR_IDENTITIES[identity]
        if (
            target != row["metric"]
            or set(parts) != set(terms)
            or any(parts[role].get("metric") != role for role in terms)
        ):
            return False, "ACCOUNTING_IDENTITY_DEFINITION_UNSUPPORTED"
        if any(p.get("unit") != "USD" or p.get("currency") != "USD" for p in leaves):
            return False, "ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH"
        if (
            identity == "PRETAX_MINUS_TAX_EQUALS_NET_INCOME"
            and item.get("scope_confirmed") is not True
        ):
            return False, "NET_INCOME_SCOPE_NOT_CONFIRMED"
        good = (
            sum((values[role] * coefficient for role, coefficient in terms.items()), Fraction())
            == 0
        )
    elif identity == "CASH_MOVEMENT":
        roles = {
            "cash_begin",
            "cash_end",
            "operating_cash_flow",
            "investing_cash_flow",
            "financing_cash_flow",
            "fx_cash_effect",
        }
        if (
            set(parts) != roles
            or row["metric"] not in ("cash", "operating_cash_flow")
            or not item.get("period_start")
        ):
            return False, "ACCOUNTING_IDENTITY_DEFINITION_UNSUPPORTED"
        if any(
            parts[role].get("metric") != ("cash" if role in ("cash_begin", "cash_end") else role)
            for role in roles
        ):
            return False, "ACCOUNTING_IDENTITY_DEFINITION_UNSUPPORTED"
        start, end = item["period_start"], item["period_end"]
        begin = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        if any(p.get("unit") != "USD" or p.get("currency") != "USD" for p in leaves):
            return False, "ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH"
        if (
            scope(parts["cash_begin"])[:2] != (None, begin)
            or scope(parts["cash_end"])[:2] != (None, end)
            or any(
                scope(parts[role])[:2] != (start, end)
                for role in roles - {"cash_begin", "cash_end"}
            )
        ):
            return False, "ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH"
        good = values["cash_end"] - values["cash_begin"] == sum(
            (values[role] for role in roles - {"cash_begin", "cash_end"}), Fraction()
        )
    elif identity == "DILUTED_SHARES_EPS":
        roles = {"diluted_shares", "eps_diluted", "income_available_to_common"}
        evidence = item.get("rounding_evidence", {})
        if (
            row["metric"] != "diluted_shares"
            or set(parts) != roles
            or item.get("income_scope") != "AVAILABLE_TO_COMMON"
        ):
            return False, "ACCOUNTING_IDENTITY_DEFINITION_UNSUPPORTED"
        if not evidence or set(evidence.get("source_ids", [])) != set(ids.values()):
            return False, "EPS_ROUNDING_EVIDENCE_REQUIRED"
        precision = [
            evidence.get(key)
            for key in ("eps_decimal_places", "shares_decimal_places", "income_decimal_places")
        ]
        if any(type(n) is not int or not 0 <= n <= 12 for n in precision):
            return False, "EPS_ROUNDING_EVIDENCE_REQUIRED"
        if [
            parts[role].get("unit")
            for role in ("diluted_shares", "eps_diluted", "income_available_to_common")
        ] != ["shares", "USD/share", "USD"]:
            return False, "ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH"
        eps_error, shares_error, income_error = [Fraction(1, 2 * 10**n) for n in precision]
        tolerance = (
            abs(values["diluted_shares"]) * eps_error
            + abs(values["eps_diluted"]) * shares_error
            + eps_error * shares_error
            + income_error
        )
        good = (
            abs(
                values["diluted_shares"] * values["eps_diluted"]
                - values["income_available_to_common"]
            )
            <= tolerance
        )
    elif identity == "DEBT_COMPONENT_SUM":
        evidence = item.get("nonoverlap_evidence", {})
        approved = set(data.get("policy", {}).get("debt_components", []))
        if (
            row["metric"] != "total_debt"
            or evidence.get("approved") is not True
            or not evidence.get("evidence_id")
            or set(evidence.get("source_ids", [])) != set(ids.values())
            or not approved
            or {p.get("concept", "").removeprefix("us-gaap:") for p in leaves} != approved
            or any(not p.get("metric", "").startswith("debt_component:") for p in leaves)
        ):
            return False, "DEBT_COMPONENT_NONOVERLAP_NOT_CONFIRMED"
        if any(p.get("unit") != "USD" or p.get("currency") != "USD" for p in leaves):
            return False, "ACCOUNTING_INPUT_FILING_OR_SCOPE_MISMATCH"
        good = sum(values.values(), Fraction()) == exact(row["value_decimal"])
    else:
        return False, "ACCOUNTING_IDENTITY_DEFINITION_UNSUPPORTED"
    return good, None if good else "ACCOUNTING_IDENTITY_VALUES_DO_NOT_RECONCILE"


def certify_history(
    data, history_result, *, as_of, reconciliation_evidence=None, accounting_inputs=()
):
    if data.get("rights") != "PUBLIC_SEC_ONLY":
        raise ValueError("CERTIFICATION_REQUIRES_PUBLIC_SEC_ONLY")
    cutoff = parse_instant(as_of).epoch_ns
    lookup = {row["source_observation_id"]: row for row in data["observations"]}
    output = []
    for row in history_result.get("observations", []):
        certified = {
            key: row.get(key)
            for key in (
                "metric",
                "frequency",
                "period_start",
                "period_end",
                "unit",
                "value_decimal",
            )
        }
        certified.update(
            status="UNAVAILABLE",
            verified_statuses=[],
            reasons=[],
            rule_ids=["SOURCE.EXACT_CONTEXT.v1"],
            source_evidence=[],
            accounting_status="NOT_ESTABLISHED",
            independent_status="NOT_ESTABLISHED",
        )
        try:
            leaves, derived = validate_sources(data, row, lookup, cutoff)
            certified["source_evidence"] = source_evidence(leaves)
            verified = ["SOURCE_OBSERVED"]
            if reconciled(data, row, reconciliation_evidence):
                verified.append("INDEPENDENTLY_RECONCILED")
                certified["independent_status"] = "PASS"
                certified["rule_ids"].append("RECONCILIATION.EXACT_ROW_SOURCE_BINDING.v1")
            if derived:
                verified.append("DERIVATION_VERIFIED")
                certified["rule_ids"].append("DERIVATION.EXACT_LINEAR_SOURCE_TERMS.v1")
                if len({(r["accession"], r["form"], r["available_at"]) for r in leaves}) > 1:
                    certified["reasons"].append("DERIVATION_INPUT_VINTAGES_DIFFER;ARITHMETIC_ONLY")
            for identity in accounting_inputs:
                good, reason = accounting_identity(data, row, identity, lookup, cutoff)
                if reason:
                    certified["reasons"].append(reason)
                    certified["accounting_status"] = (
                        "FAIL"
                        if reason == "ACCOUNTING_IDENTITY_VALUES_DO_NOT_RECONCILE"
                        else "UNAVAILABLE"
                    )
                if good:
                    verified.append("ACCOUNTING_CERTIFIED")
                    certified["accounting_status"] = "PASS"
                    certified["rule_ids"].append("ACCOUNTING." + identity["identity_id"] + ".v1")
                    certified["accounting_evidence"] = identity
                    break
            certified["verified_statuses"] = sorted(set(verified), key=RANK.index)
            certified["status"] = certified["verified_statuses"][-1]
        except (ValueError, TypeError, KeyError, InvalidOperation) as error:
            certified["reasons"].append(
                str(error)
                if isinstance(error, ValueError)
                else "INCOMPLETE_SOURCE_OR_IDENTITY_CONTEXT"
            )
        certified["version"] = VERSION
        output.append(certified)
    gaps = [
        {
            **gap,
            "status": "UNAVAILABLE",
            "verified_statuses": [],
            "version": VERSION,
            "rule_ids": ["COVERAGE.NO_IMPUTATION.v1"],
            "reasons": [gap.get("reason", "SOURCE_NOT_AVAILABLE")],
            "source_evidence": [],
        }
        for gap in history_result.get("coverage_gaps", [])
    ]
    return {
        "version": VERSION,
        "schema_version": VERSION,
        "cik": data["cik"],
        "as_of": parse_instant(as_of).iso_utc,
        "observations": output,
        "coverage_gaps": gaps,
        "summary": dict(Counter(row["status"] for row in output + gaps)),
        "qualification": (
            "METRIC_AND_PERIOD_EVIDENCE_ONLY;"
            "BALANCE_TOTAL_PASS_IS_NOT_INCOME_OR_CASH_CERTIFICATION;NO_IMPUTATION"
        ),
    }
