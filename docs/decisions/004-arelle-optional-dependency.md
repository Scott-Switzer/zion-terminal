# ADR-004: Arelle as Optional XBRL Dependency

## Status
Accepted (v0.3.0)

## Context
Arelle is a full-featured XBRL processor that can validate SEC filing XBRL
instance documents. It's a large dependency (~50MB+ with taxonomies) and
requires specific setup. Most users of Zion Terminal don't need XBRL
validation — they need stock quotes, financials, and macro data.

## Decision
- Arelle is an optional dependency under `[xbrl]` extra
- `pipeline/xbrl.py` wraps Arelle with a thin `XBRLVerifier` class
- All functions check `_ARELLE_AVAILABLE` and return graceful errors if missing
- The system works completely without Arelle installed

Install command: `pip install zion-terminal[xbrl]`

## Consequences
- Default install stays lightweight
- XBRL validation is available for users who need it
- No import errors if Arelle is missing
- Future: Arelle can be used for automated financial statement validation
