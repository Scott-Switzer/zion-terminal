# ADR-0008: Arelle Validation Boundaries

## Status
Accepted (updated v0.4.2)

## Context
Arelle is a full XBRL processor. The project uses it for two purposes:
1. Validating XBRL instance documents against their schemas
2. Extracting structured facts from filings

The project needed to define clear boundaries for what Arelle validates and what it does not.

## Decision
Arelle is integrated into the live filing pipeline via `pipeline/verification.py` → `pipeline/xbrl.py`. It operates as Tier 2 (XBRL) verification.

### What Arelle validates:
- Schema conformance of XBRL instance documents
- Fact extraction: concept name, value (typed via xValue), context, unit, period
- Loading errors and validation errors

### What Arelle does NOT validate:
- Semantic correctness of financial values
- Consistency between XBRL facts and narrative markdown text
- Completeness of disclosure
- Cross-period consistency

### Correct API patterns used:
- `fact.concept.qname.localName` for concept name
- `fact.xValue` for typed Python value (preferred over `fact.value`)
- `fact.context.startDatetime` / `.endDatetime` / `.instantDatetime`
- `str(fact.unit.measures[0][0])` for unit string
- `ModelManager.initialize(ctrl).load(source)` for loading
- `ValidateXbrl.ValidateXbrl(model_xbrl).validate()` for validation

## Alternatives Considered
1. **Use Arelle as full validation engine**: Too complex for current phase.
2. **Skip Arelle entirely**: Loses the most valuable verification signal.
3. **Build custom XBRL parser**: Reinventing the wheel; Arelle is the standard.

## Tradeoffs
- Arelle is a heavy optional dependency
- Not all XBRL documents validate cleanly (some SEC filings have known schema issues)
- Graceful degradation when Arelle not installed prevents hard failures

## Consequences
- XBRL verification is real but bounded
- `xbrl_status` in verification results clearly reports what happened
- Tests use mocks when Arelle is not installed
- Real Arelle testing requires `pip install zion-terminal[xbrl]`

## Follow-up Work Required
- Wire iXBRL validation (Arelle supports it)
- Compare Arelle-extracted facts against markdown-derived values
- Build fact-to-markdown reconciliation logic
