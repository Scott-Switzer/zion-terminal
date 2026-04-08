"""Tests for source role model and SEC-first architecture."""
import pytest
from zion_terminal.models.source_roles import (
    SourceRole, DataDomain, SOURCE_ROLES, get_source_role, SourceAttribution,
)


class TestSourceRoles:
    def test_sec_is_primary_for_financials(self):
        role = get_source_role("sec_edgar", DataDomain.FINANCIAL_STATEMENTS)
        assert role == SourceRole.PRIMARY

    def test_sec_is_primary_for_filing_content(self):
        role = get_source_role("sec_edgar", DataDomain.FILING_CONTENT)
        assert role == SourceRole.PRIMARY

    def test_sec_is_primary_for_company_facts(self):
        role = get_source_role("sec_edgar", DataDomain.COMPANY_FACTS)
        assert role == SourceRole.PRIMARY

    def test_yahoo_is_primary_for_market_data(self):
        role = get_source_role("yahoo_finance", DataDomain.MARKET_DATA)
        assert role == SourceRole.PRIMARY

    def test_yahoo_is_verification_for_financials(self):
        role = get_source_role("yahoo_finance", DataDomain.FINANCIAL_STATEMENTS)
        assert role == SourceRole.VERIFICATION

    def test_fred_is_primary_for_macro(self):
        role = get_source_role("fred", DataDomain.MACRO_DATA)
        assert role == SourceRole.PRIMARY

    def test_unknown_source_returns_fallback(self):
        role = get_source_role("unknown_source", DataDomain.MARKET_DATA)
        assert role == SourceRole.FALLBACK

    def test_source_attribution_model(self):
        attr = SourceAttribution(
            source="sec_edgar",
            role=SourceRole.PRIMARY,
            domain=DataDomain.FINANCIAL_STATEMENTS,
        )
        assert attr.verification_status == "not_verified"
        assert attr.verified_by == []
