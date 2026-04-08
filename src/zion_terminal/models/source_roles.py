"""Source role definitions — which data source is authoritative for what."""
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class SourceRole(str, Enum):
    PRIMARY = "primary"           # Authoritative source of truth
    VERIFICATION = "verification" # Cross-check / sanity check
    FALLBACK = "fallback"         # Used when primary unavailable
    CONVENIENCE = "convenience"   # Quick access, not authoritative

class DataDomain(str, Enum):
    FINANCIAL_STATEMENTS = "financial_statements"
    FILING_CONTENT = "filing_content"
    COMPANY_FACTS = "company_facts"
    MARKET_DATA = "market_data"
    MACRO_DATA = "macro_data"
    COMPANY_INFO = "company_info"

# The source-of-truth matrix
SOURCE_ROLES: dict[str, dict[str, SourceRole]] = {
    "sec_edgar": {
        DataDomain.FINANCIAL_STATEMENTS: SourceRole.PRIMARY,
        DataDomain.FILING_CONTENT: SourceRole.PRIMARY,
        DataDomain.COMPANY_FACTS: SourceRole.PRIMARY,
        DataDomain.COMPANY_INFO: SourceRole.VERIFICATION,
    },
    "yahoo_finance": {
        DataDomain.MARKET_DATA: SourceRole.PRIMARY,
        DataDomain.FINANCIAL_STATEMENTS: SourceRole.VERIFICATION,
        DataDomain.COMPANY_INFO: SourceRole.CONVENIENCE,
    },
    "fred": {
        DataDomain.MACRO_DATA: SourceRole.PRIMARY,
    },
}

def get_source_role(source: str, domain: str) -> SourceRole:
    """Look up what role a source plays for a given data domain."""
    return SOURCE_ROLES.get(source, {}).get(domain, SourceRole.FALLBACK)

class SourceAttribution(BaseModel):
    """Attached to every data item to track provenance."""
    source: str
    role: SourceRole
    domain: DataDomain
    verified_by: list[str] = Field(default_factory=list)
    verification_status: str = "not_verified"
