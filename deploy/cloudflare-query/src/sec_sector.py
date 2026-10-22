"""SEC industry grouping: one vocabulary shared by ingest, classification and the screen.

The Company Facts archive carries no industry identifier, so a sector question cannot be answered
from it at all. The identifier comes from public SEC sources instead, all of which are pinned by
digest when they are ingested:

* the quarterly Financial Statement Data Sets, whose ``sub.txt`` states the ``sic`` of every filer
  that filed a financial statement in that quarter;
* the SEC's Standard Industrial Classification code list, which states the industry title of each
  code and the Commission office that reviews it.

Neither source is a valuation input: an industry code never changes an evidence state, an
eligibility verdict or a claim value. It only says which population a verdict belongs to, which is
why it lives beside the readiness record rather than inside the claims contract.

This module is the vocabulary itself, so the API validates a filter against the same names the
classifier writes and the ingest refuses a source whose office labels are new.
"""

SIC_LIST_URL = "https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list"
FSDS_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/%s.zip"

# The office labels are the ones the pinned code list actually uses, including its composite
# labels. A label outside this table is refused rather than folded into a catch-all: a source that
# renamed an office is a fact about the source, not a sector the issuers belong to.
OFFICE_GROUPS = {
    "Industrial Applications and Services": "industrial_applications_and_services",
    "Office of Energy & Transportation": "energy_and_transportation",
    "Office of Manufacturing": "manufacturing",
    "Office of Trade & Services": "trade_and_services",
    "Office of Technology": "technology",
    "Office of Finance": "finance",
    "Office of Real Estate & Construction": "real_estate_and_construction",
    "Office of Life Sciences": "life_sciences",
    "Office of International Corp Fin": "international_corp_fin",
    "Office of Crypto Assets": "crypto_assets",
    "Office of Structured Finance": "structured_finance",
    # A code the Commission assigns to two offices at once is reported as such. Choosing one of
    # them would invent a sector the source does not state.
    "Office of Finance or Office of Crypto Assets": "multiple_offices",
    "Office of Trade & Services or Office of Energy & Transportation": "multiple_offices",
    "Multiple Offices": "multiple_offices",
}
MULTIPLE_OFFICES = "multiple_offices"
SECTOR_GROUPS = tuple(sorted(set(OFFICE_GROUPS.values())))
# The groups an issuer is placed in when no sector is stated for it. They are filterable like any
# other group, because "which issuers have no industry identifier" is a question worth asking.
IDENTIFIER_ABSENT_GROUPS = ("unlisted_sic_code", "NOT_IN_THE_INDUSTRY_SOURCE",
                            "NO_PUBLISHED_INDUSTRY_RELEASE")


def office(office_label):
    """The grouping slug for one office label from the pinned code list.

    An unlisted label raises rather than defaulting: silently folding a renamed office into a
    catch-all group would attribute issuers to a sector the source never stated.
    """
    key = normalized(office_label)
    if key not in OFFICE_GROUPS:
        raise ValueError("UNMAPPED_SEC_OFFICE:" + key)
    return OFFICE_GROUPS[key]


def normalized(office_label):
    """The label as the code list means it: non-breaking spaces collapsed, no stray padding."""
    return " ".join(str(office_label or "").replace("\u00a0", " ").split())
