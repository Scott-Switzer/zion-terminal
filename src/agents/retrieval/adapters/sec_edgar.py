"""SEC EDGAR adapter using edgartools.

Covers: 10-K, 10-Q, 8-K, proxy statements, and company facts / financials
from the SEC EDGAR database.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from src.agents.retrieval.base_adapter import BaseAdapter
from src.models.financial import FilingType, SECFiling
from src.models.responses import RetrievalResult

logger = logging.getLogger(__name__)

_FORM_MAP: dict[str, FilingType] = {
    "10-K": FilingType.TEN_K,
    "10-Q": FilingType.TEN_Q,
    "8-K": FilingType.EIGHT_K,
    "DEF 14A": FilingType.PROXY,
}


class SECEdgarAdapter(BaseAdapter):
    """Fetch SEC filings and structured financials via edgartools."""

    SOURCE_NAME = "sec_edgar"
    SUPPORTED_CATEGORIES = ["sec", "filings", "edgar", "10k", "10q", "8k"]

    def __init__(self, identity: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._identity = identity

    def _init_edgar(self) -> None:
        """Set EDGAR identity (required by SEC)."""
        from edgar import set_identity
        if self._identity:
            set_identity(self._identity)

    def fetch(self, params: dict[str, Any]) -> RetrievalResult:
        """Route to filings list, filing detail, or company financials."""
        action = params.get("action", "filings")
        ticker = params.get("ticker", "").upper()

        if not ticker:
            return RetrievalResult(
                success=False,
                errors=["Missing required parameter: ticker"],
            )

        cached = self._cache_get(params)
        if cached is not None:
            return RetrievalResult(data=cached, sources_used=[self.SOURCE_NAME], cached=True)

        try:
            self._init_edgar()

            if action == "filings":
                result = self._fetch_filings(ticker, params)
            elif action == "financials":
                result = self._fetch_financials(ticker, params)
            elif action == "company_facts":
                result = self._fetch_company_facts(ticker)
            else:
                return RetrievalResult(
                    success=False,
                    errors=[f"Unknown SEC action: {action}"],
                )

            if result.success:
                self._cache_set(params, result.data)
            return result
        except Exception as exc:
            logger.exception("SEC EDGAR fetch failed for %s", ticker)
            return RetrievalResult(
                success=False,
                errors=[f"SEC EDGAR error: {exc}"],
            )

    def _fetch_filings(self, ticker: str, params: dict) -> RetrievalResult:
        """List recent filings for a company, optionally filtered by form."""
        from edgar import Company

        company = Company(ticker)
        form_filter = params.get("form")  # e.g. "10-K"
        limit = params.get("limit", 10)

        filings = company.get_filings()
        if form_filter:
            filings = filings.filter(form=form_filter)

        results: list[dict[str, Any]] = []
        for i, filing in enumerate(filings):
            if i >= limit:
                break

            filing_type = _FORM_MAP.get(str(filing.form), FilingType.OTHER)
            filing_date_val = None
            if hasattr(filing, "filing_date") and filing.filing_date:
                try:
                    if isinstance(filing.filing_date, date):
                        filing_date_val = filing.filing_date
                    else:
                        filing_date_val = date.fromisoformat(str(filing.filing_date))
                except (ValueError, TypeError):
                    pass

            sec_filing = SECFiling(
                ticker=ticker,
                company_name=str(getattr(company, "name", ticker)),
                cik=str(getattr(company, "cik", "")),
                filing_type=filing_type,
                filing_date=filing_date_val,
                accession_number=str(getattr(filing, "accession_no", "")),
                document_url=str(getattr(filing, "homepage_url", "")),
                description=str(getattr(filing, "form", "")) + " filing",
                source=self.SOURCE_NAME,
            )
            results.append(sec_filing.model_dump())

        return RetrievalResult(data=results, sources_used=[self.SOURCE_NAME])

    def _fetch_financials(self, ticker: str, params: dict) -> RetrievalResult:
        """Fetch structured financial data from XBRL filings."""
        from edgar import Company

        company = Company(ticker)
        filings = company.get_filings(form="10-K")

        results: list[dict[str, Any]] = []
        limit = params.get("limit", 3)

        for i, filing in enumerate(filings):
            if i >= limit:
                break
            try:
                filing_obj = filing.obj()
                if hasattr(filing_obj, "financials") and filing_obj.financials is not None:
                    financials = filing_obj.financials

                    for stmt_name in ["income_statement", "balance_sheet", "cash_flow_statement"]:
                        stmt = getattr(financials, stmt_name, None)
                        if stmt is not None:
                            try:
                                df = stmt.to_dataframe() if hasattr(stmt, "to_dataframe") else None
                                if df is not None and not df.empty:
                                    results.append({
                                        "ticker": ticker,
                                        "type": "financial_statement",
                                        "statement": stmt_name,
                                        "filing_date": str(getattr(filing, "filing_date", "")),
                                        "data": df.to_dict(),
                                        "source": self.SOURCE_NAME,
                                    })
                            except Exception:
                                # Some filings may not parse cleanly
                                results.append({
                                    "ticker": ticker,
                                    "type": "financial_statement",
                                    "statement": stmt_name,
                                    "filing_date": str(getattr(filing, "filing_date", "")),
                                    "data": str(stmt)[:2000],
                                    "source": self.SOURCE_NAME,
                                })
            except Exception as exc:
                logger.warning("Could not parse filing %d for %s: %s", i, ticker, exc)
                continue

        if not results:
            return RetrievalResult(
                success=False,
                errors=[f"No parseable financial data found for {ticker}"],
            )

        return RetrievalResult(data=results, sources_used=[self.SOURCE_NAME])

    def _fetch_company_facts(self, ticker: str) -> RetrievalResult:
        """Fetch company facts (XBRL data) from SEC."""
        from edgar import Company

        company = Company(ticker)
        facts = company.get_facts()

        facts_data = {
            "ticker": ticker,
            "type": "company_facts",
            "company_name": str(getattr(company, "name", ticker)),
            "cik": str(getattr(company, "cik", "")),
            "source": self.SOURCE_NAME,
        }

        if facts is not None:
            try:
                df = facts.to_pandas() if hasattr(facts, "to_pandas") else None
                if df is not None and not df.empty:
                    # Get the most recent facts
                    facts_data["facts_count"] = len(df)
                    facts_data["sample_facts"] = df.head(20).to_dict(orient="records")
                else:
                    facts_data["facts_summary"] = str(facts)[:3000]
            except Exception:
                facts_data["facts_summary"] = str(facts)[:3000]

        return RetrievalResult(data=[facts_data], sources_used=[self.SOURCE_NAME])
