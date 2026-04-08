# ADR-008: Arelle API Alignment

## Status
Accepted

## Context
The XBRL verifier wrapper assumed `fact.concept` was a dict-like object and called `.get("name", "")` on it.  In Arelle's actual API, `fact.concept` is a `ModelConcept` object.  The concept name lives at `fact.concept.qname.localName`.

Period info was accessed via a nested `fact.context.period` sub-object, but Arelle exposes `startDatetime`, `endDatetime`, and `instantDatetime` directly on the `ModelContext`.

## Decision
1. Extracted concept name via `fact.concept.qname.localName` with fallback to `fact.concept.name`.
2. Extracted period info via `context.isStartEndPeriod` / `context.isInstantPeriod` and the corresponding datetime properties directly on context.
3. Both extraction methods are static methods with defensive `getattr` calls so they degrade gracefully if the Arelle API changes.
4. Added 7 positive-path unit tests using mocks that simulate real Arelle objects.

## Consequences
- XBRL fact extraction will now work correctly when Arelle is installed.
- Arelle remains optional — the graceful fallback for missing Arelle is unchanged.
- Experimental status unchanged; Arelle integration is still opt-in.
