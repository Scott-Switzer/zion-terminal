# ADR-0013: Arelle in Live Validation

## Status
Accepted

## Context
Arelle existed as a thin wrapper module but was not part of the live filing pipeline.

## Decision
Arelle is now integrated into `FilingVerifier` as Tier 2 (XBRL) verification. When a XBRL URL is available, the verifier calls `XBRLVerifier.validate_url()` to validate the instance document and extract facts.

When Arelle is not installed, verification degrades gracefully with `xbrl_status = "unavailable"`.

## Consequences
- Arelle remains an optional dependency (`pip install zion-terminal[xbrl]`)
- XBRL verification is real — if Arelle reports errors, they appear in the response
- No fake validation: missing Arelle = "unavailable", not "passed"
- Fact extraction uses the correct Arelle API (`fact.concept.qname.localName`)
