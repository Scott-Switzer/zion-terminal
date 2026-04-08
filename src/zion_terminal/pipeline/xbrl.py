"""Arelle XBRL verification integration.

Status: experimental, optional dependency.
Install via: pip install zion-terminal[xbrl]

This module wraps Arelle to validate XBRL instance documents and
extract structured facts. Arelle is a full XBRL processor — this
module only exposes a thin verification surface for:

1. Validating that an XBRL instance document is schema-conformant.
2. Extracting facts (concept + value + context) into a flat list.

If arelle-release is not installed, all functions return graceful
errors instead of crashing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_ARELLE_AVAILABLE = False
try:
    from arelle import Cntlr, ModelDocument
    _ARELLE_AVAILABLE = True
except ImportError:
    pass


def is_available() -> bool:
    """Check if Arelle is installed and importable."""
    return _ARELLE_AVAILABLE


@dataclass
class XBRLFact:
    """A single XBRL fact extracted from a filing."""
    concept: str
    value: str | float | None
    context_id: str | None = None
    unit: str | None = None
    decimals: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    period_instant: str | None = None


@dataclass
class XBRLValidationResult:
    """Result of XBRL validation."""
    valid: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    facts_count: int = 0
    facts: list[XBRLFact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class XBRLVerifier:
    """Thin wrapper around Arelle for XBRL validation.

    Usage::

        verifier = XBRLVerifier()
        if not verifier.is_available:
            print("Install arelle-release for XBRL support")

        result = verifier.validate_url(filing_url)
        if result.valid:
            for fact in result.facts:
                print(f"{fact.concept}: {fact.value}")
    """

    @property
    def is_available(self) -> bool:
        return _ARELLE_AVAILABLE

    def validate_url(self, url: str, extract_facts: bool = True) -> XBRLValidationResult:
        """Validate an XBRL instance document by URL.

        Args:
            url: URL to the XBRL instance document.
            extract_facts: If True, also extract all facts.

        Returns:
            XBRLValidationResult with validation status and optionally facts.
        """
        if not _ARELLE_AVAILABLE:
            return XBRLValidationResult(
                valid=False,
                errors=["Arelle is not installed. Install via: pip install zion-terminal[xbrl]"],
            )

        try:
            return self._run_validation(url, extract_facts)
        except Exception as exc:
            logger.exception("XBRL validation failed for %s", url)
            return XBRLValidationResult(
                valid=False,
                errors=[f"XBRL validation error: {exc}"],
            )

    def validate_file(self, path: str, extract_facts: bool = True) -> XBRLValidationResult:
        """Validate a local XBRL file."""
        return self.validate_url(path, extract_facts)

    @staticmethod
    def _extract_concept_name(fact) -> str:
        """Safely extract the concept name from an Arelle ModelFact.

        In Arelle's API, ``fact.concept`` is a ``ModelConcept`` object
        (not a dict).  The canonical name lives at ``fact.concept.qname``
        which is a ``QName`` whose ``str()`` gives "prefix:localName".
        We prefer the local name only for readability.
        """
        concept = getattr(fact, "concept", None)
        if concept is None:
            return ""
        qname = getattr(concept, "qname", None)
        if qname is not None:
            # qname.localName is the unqualified tag name
            return getattr(qname, "localName", str(qname))
        # Last-resort fallback
        name = getattr(concept, "name", None)
        return str(name) if name else ""

    @staticmethod
    def _extract_period_info(fact, xbrl_fact: XBRLFact) -> None:
        """Populate period fields on *xbrl_fact* from the Arelle context.

        Arelle's ``ModelContext`` exposes period info via properties
        ``startDatetime``, ``endDatetime``, and ``instantDatetime``
        directly on the context, not on a nested ``period`` sub-object.
        """
        ctx = getattr(fact, "context", None)
        if ctx is None:
            return
        # Arelle puts period datetimes directly on context
        if getattr(ctx, "isStartEndPeriod", False):
            sd = getattr(ctx, "startDatetime", None)
            ed = getattr(ctx, "endDatetime", None)
            if sd is not None:
                xbrl_fact.period_start = str(sd)
            if ed is not None:
                xbrl_fact.period_end = str(ed)
        elif getattr(ctx, "isInstantPeriod", False):
            inst = getattr(ctx, "instantDatetime", None)
            if inst is not None:
                xbrl_fact.period_instant = str(inst)

    def _run_validation(self, source: str, extract_facts: bool) -> XBRLValidationResult:
        """Run Arelle validation on a source (URL or file path).

        Uses correct Arelle Python API patterns:
          fact.concept.qname.localName  — concept name
          fact.value / fact.xValue      — raw/typed value
          fact.context.startDatetime    — period start
          fact.context.endDatetime      — period end
          fact.context.instantDatetime  — instant date
          str(fact.unit.measures[0][0]) — unit string
        """
        from arelle import Cntlr, ModelManager

        ctrl = Cntlr.Cntlr(logFileName="logToPrint")
        ctrl.startLogging(logFileName="logToPrint")
        model_xbrl = ModelManager.initialize(ctrl).load(source)

        errors: list[str] = []
        warnings: list[str] = []

        if model_xbrl is None:
            return XBRLValidationResult(
                valid=False,
                errors=[f"Could not load XBRL document from {source}"],
            )

        # Run validation
        try:
            from arelle import ValidateXbrl
            validator = ValidateXbrl.ValidateXbrl(model_xbrl)
            validator.validate()
        except Exception as exc:
            warnings.append(f"Validation step failed: {exc}")

        # Check for loading/validation errors
        if hasattr(model_xbrl, "errors") and model_xbrl.errors:
            for err in model_xbrl.errors:
                errors.append(str(err))

        # Extract facts if requested
        facts: list[XBRLFact] = []
        if extract_facts and hasattr(model_xbrl, "facts"):
            for fact in model_xbrl.facts:
                try:
                    xbrl_fact = XBRLFact(
                        concept=self._extract_concept_name(fact),
                        value=getattr(fact, "xValue", None) or getattr(fact, "value", None),
                        context_id=str(getattr(fact, "contextID", "")),
                        unit=self._extract_unit(fact),
                        decimals=str(getattr(fact, "decimals", "")),
                    )
                    self._extract_period_info(fact, xbrl_fact)
                    facts.append(xbrl_fact)
                except Exception:
                    continue

        # Close the controller
        try:
            ctrl.close()
        except Exception:
            pass

        return XBRLValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            facts_count=len(facts),
            facts=facts,
            metadata={"source": source},
        )

    @staticmethod
    def _extract_unit(fact) -> str | None:
        """Extract unit string from Arelle fact using correct API."""
        unit = getattr(fact, "unit", None)
        if unit is None:
            return None
        try:
            measures = getattr(unit, "measures", None)
            if measures and len(measures) > 0 and len(measures[0]) > 0:
                return str(measures[0][0])
        except Exception:
            pass
        return str(unit) if unit else None
