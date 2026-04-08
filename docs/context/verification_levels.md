# Verification Levels

## Level Definitions

| Level | Name | Description | Implemented |
|---|---|---|---|
| L0 | Exists | Data item is non-empty | Yes |
| L1 | Attributed | Source and metadata present | Yes |
| L2 | Bounded | Values within expected ranges | Yes (market data) |
| L3 | Structurally Complete | Filing has expected sections | Yes (v0.4.0) |
| L4 | Schema Valid | XBRL passes schema validation | Yes (optional dep) |
| L5 | Cross-Source Consistent | SEC and Yahoo agree on key values | Hook only |
| L6 | Historically Consistent | Values align with prior filings | Not implemented |
| L7 | Expert Validated | Compared against Bloomberg | Not implemented (internal only) |

## How Levels Map to Code

- L0-L2: `agents/validation/agent.py` — `ValidationAgent.validate_retrieval()`
- L3-L4: `pipeline/verification.py` — `FilingVerifier.verify()`
- L5: `pipeline/verification.py` — `_check_cross_source()` (hook)
- L6-L7: Not yet implemented
