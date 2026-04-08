"""Map XBRL facts to a canonical schema for reconciliation."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CanonicalFact:
    """XBRL fact in canonical form for comparison."""
    concept: str = ""           # e.g. "us-gaap:Revenues"
    local_name: str = ""        # e.g. "Revenues"
    value: float | str | None = None
    unit: str | None = None     # e.g. "USD"
    period_type: str = ""       # "instant" | "duration"
    period_start: str | None = None
    period_end: str | None = None
    namespace: str = ""
    decimals: int | None = None
    scale: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "local_name": self.local_name,
            "value": self.value,
            "unit": self.unit,
            "period_type": self.period_type,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "namespace": self.namespace,
            "decimals": self.decimals,
            "scale": self.scale,
        }


def map_xbrl_fact(fact) -> CanonicalFact:
    """Convert an Arelle ModelFact to canonical form.
    
    Uses correct Arelle API:
      fact.concept.qname.localName
      fact.concept.qname.namespaceURI
      fact.xValue (typed) or fact.value (raw string)
      fact.context.startDatetime / endDatetime / instantDatetime
      fact.unit.measures[0][0]
    """
    concept_name = ""
    namespace = ""
    concept = getattr(fact, "concept", None)
    if concept is not None:
        qname = getattr(concept, "qname", None)
        if qname:
            concept_name = f"{getattr(qname, 'prefix', '')}:{getattr(qname, 'localName', '')}"
            namespace = getattr(qname, "namespaceURI", "")

    local_name = ""
    if concept and hasattr(concept, "qname") and concept.qname:
        local_name = getattr(concept.qname, "localName", "")

    # Value: prefer typed xValue over raw string value
    value = getattr(fact, "xValue", None)
    if value is None:
        value = getattr(fact, "value", None)

    # Unit
    unit_str = None
    unit_obj = getattr(fact, "unit", None)
    if unit_obj is not None:
        try:
            measures = getattr(unit_obj, "measures", None)
            if measures and len(measures) > 0 and len(measures[0]) > 0:
                unit_str = str(measures[0][0])
        except Exception:
            unit_str = str(unit_obj)

    # Period
    period_type = ""
    period_start = None
    period_end = None
    ctx = getattr(fact, "context", None)
    if ctx is not None:
        if getattr(ctx, "isStartEndPeriod", False):
            period_type = "duration"
            sd = getattr(ctx, "startDatetime", None)
            ed = getattr(ctx, "endDatetime", None)
            if sd: period_start = str(sd.date()) if hasattr(sd, "date") else str(sd)
            if ed: period_end = str(ed.date()) if hasattr(ed, "date") else str(ed)
        elif getattr(ctx, "isInstantPeriod", False):
            period_type = "instant"
            inst = getattr(ctx, "instantDatetime", None)
            if inst: period_end = str(inst.date()) if hasattr(inst, "date") else str(inst)

    decimals = None
    dec_str = getattr(fact, "decimals", None)
    if dec_str and dec_str not in ("INF", "inf", None):
        try:
            decimals = int(dec_str)
        except (ValueError, TypeError):
            pass

    return CanonicalFact(
        concept=concept_name,
        local_name=local_name,
        value=value,
        unit=unit_str,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        namespace=namespace,
        decimals=decimals,
    )
