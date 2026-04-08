"""SEC EDGAR adapter using edgartools.

Status: working. Requires EDGAR_IDENTITY (name + email) per SEC policy.
Rate limit: SEC enforces ~10 req/sec; edgartools handles this internally.
Filing-to-markdown conversion is experimental (Phase 1.5).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from zion_terminal.agents.retrieval.base_adapter import BaseAdapter
from zion_terminal.models.financial import FilingType, SECFiling
from zion_terminal.models.responses import RetrievalResult

logger = logging.getLogger(__name__)

_FORM_MAP: dict[str, FilingType] = {
    "10-K": FilingType.TEN_K, "10-Q": FilingType.TEN_Q,
    "8-K": FilingType.EIGHT_K, "DEF 14A": FilingType.PROXY,
}

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    reraise=True,
)

# Simple rate limiter: track last request time
_last_request_time: float = 0.0
_MIN_REQUEST_INTERVAL: float = 0.12  # ~8 req/sec to stay under SEC's 10/sec


def _rate_limit() -> None:
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


class SECEdgarAdapter(BaseAdapter):
    SOURCE_NAME = "sec_edgar"
    SUPPORTED_CATEGORIES = ["sec", "filings", "edgar", "10k", "10q", "8k"]

    def __init__(self, identity: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._identity = identity

    def _init_edgar(self) -> None:
        from edgar import set_identity
        if self._identity:
            set_identity(self._identity)

    def fetch(self, params: dict[str, Any]) -> RetrievalResult:
        action = params.get("action", "filings")
        ticker = params.get("ticker", "").upper()
        if not ticker:
            return RetrievalResult(success=False, errors=["Missing required parameter: ticker"])

        cached = self._cache_get(params)
        if cached is not None:
            return RetrievalResult(data=cached, sources_used=[self.SOURCE_NAME], cached=True)

        try:
            self._init_edgar()
            dispatch = {
                "filings": self._fetch_filings,
                "financials": self._fetch_financials,
                "company_facts": self._fetch_company_facts,
                "filing_markdown": self._fetch_filing_markdown,
            }
            handler = dispatch.get(action)
            if handler is None:
                return RetrievalResult(success=False, errors=[f"Unknown SEC action: {action}"])

            result = handler(ticker, params)
            if result.success:
                self._cache_set(params, result.data)
            return result
        except Exception as exc:
            logger.exception("SEC EDGAR fetch failed for %s", ticker)
            return RetrievalResult(success=False, errors=[f"SEC EDGAR error: {exc}"])

    @_RETRY
    def _fetch_filings(self, ticker: str, params: dict) -> RetrievalResult:
        from edgar import Company
        _rate_limit()

        company = Company(ticker)
        form_filter = params.get("form")
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
                    filing_date_val = filing.filing_date if isinstance(filing.filing_date, date) else date.fromisoformat(str(filing.filing_date))
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
                description=f"{filing.form} filing",
                source=self.SOURCE_NAME,
            )
            results.append(sec_filing.model_dump())

        return RetrievalResult(data=results, sources_used=[self.SOURCE_NAME])

    @_RETRY
    def _fetch_financials(self, ticker: str, params: dict) -> RetrievalResult:
        from edgar import Company
        _rate_limit()

        company = Company(ticker)
        filings = company.get_filings(form="10-K")
        limit = params.get("limit", 3)
        results: list[dict[str, Any]] = []

        for i, filing in enumerate(filings):
            if i >= limit:
                break
            _rate_limit()
            try:
                filing_obj = filing.obj()
                if hasattr(filing_obj, "financials") and filing_obj.financials is not None:
                    for stmt_name in ["income_statement", "balance_sheet", "cash_flow_statement"]:
                        stmt = getattr(filing_obj.financials, stmt_name, None)
                        if stmt is not None:
                            try:
                                df = stmt.to_dataframe() if hasattr(stmt, "to_dataframe") else None
                                if df is not None and not df.empty:
                                    results.append({
                                        "ticker": ticker, "type": "financial_statement",
                                        "statement": stmt_name,
                                        "filing_date": str(getattr(filing, "filing_date", "")),
                                        "data": df.to_dict(), "source": self.SOURCE_NAME,
                                    })
                            except Exception:
                                results.append({
                                    "ticker": ticker, "type": "financial_statement",
                                    "statement": stmt_name,
                                    "filing_date": str(getattr(filing, "filing_date", "")),
                                    "data": str(stmt)[:2000], "source": self.SOURCE_NAME,
                                })
            except Exception as exc:
                logger.warning("Could not parse filing %d for %s: %s", i, ticker, exc)
                continue

        if not results:
            return RetrievalResult(success=False, errors=[f"No parseable financial data found for {ticker}"])
        return RetrievalResult(data=results, sources_used=[self.SOURCE_NAME])

    @_RETRY
    def _fetch_company_facts(self, ticker: str, params: dict) -> RetrievalResult:
        from edgar import Company
        _rate_limit()

        company = Company(ticker)
        facts = company.get_facts()
        facts_data: dict[str, Any] = {
            "ticker": ticker, "type": "company_facts",
            "company_name": str(getattr(company, "name", ticker)),
            "cik": str(getattr(company, "cik", "")),
            "source": self.SOURCE_NAME,
        }

        if facts is not None:
            try:
                df = facts.to_pandas() if hasattr(facts, "to_pandas") else None
                if df is not None and not df.empty:
                    facts_data["facts_count"] = len(df)
                    facts_data["sample_facts"] = df.head(20).to_dict(orient="records")
                else:
                    facts_data["facts_summary"] = str(facts)[:3000]
            except Exception:
                facts_data["facts_summary"] = str(facts)[:3000]

        return RetrievalResult(data=[facts_data], sources_used=[self.SOURCE_NAME])

    @_RETRY
    def _fetch_filing_markdown(self, ticker: str, params: dict) -> RetrievalResult:
        """Phase 1.5: Fetch a specific filing and convert to markdown.

        This is experimental. It fetches the primary document of the most
        recent filing of the requested type and does best-effort HTML→markdown
        conversion, preserving tables where possible.
        """
        from edgar import Company
        _rate_limit()

        company = Company(ticker)
        form = params.get("form", "10-K")
        filings = company.get_filings(form=form)

        if not filings:
            return RetrievalResult(success=False, errors=[f"No {form} filings found for {ticker}"])

        filing = filings[0]
        _rate_limit()

        try:
            # Get the filing HTML content
            filing_obj = filing.obj()
            html_content = None

            # Try to get the document text
            if hasattr(filing_obj, "text"):
                html_content = filing_obj.text
            elif hasattr(filing_obj, "html"):
                html_content = filing_obj.html
            elif hasattr(filing, "html"):
                html_content = filing.html()

            if not html_content:
                return RetrievalResult(
                    success=False,
                    errors=[f"Could not extract content from {form} filing for {ticker}"],
                )

            # Convert HTML to markdown (best-effort)
            markdown = _html_to_markdown(html_content)

            filing_data = SECFiling(
                ticker=ticker,
                company_name=str(getattr(company, "name", ticker)),
                cik=str(getattr(company, "cik", "")),
                filing_type=_FORM_MAP.get(form, FilingType.OTHER),
                filing_date=filing.filing_date if isinstance(getattr(filing, "filing_date", None), date) else None,
                accession_number=str(getattr(filing, "accession_no", "")),
                document_url=str(getattr(filing, "homepage_url", "")),
                description=f"{form} filing (markdown conversion)",
                content_markdown=markdown[:100_000],  # cap at 100k chars
                source=self.SOURCE_NAME,
            )
            return RetrievalResult(data=[filing_data.model_dump()], sources_used=[self.SOURCE_NAME])

        except Exception as exc:
            logger.warning("Filing markdown conversion failed for %s: %s", ticker, exc)
            return RetrievalResult(
                success=False,
                errors=[f"Filing markdown conversion failed: {exc}"],
            )


def _html_to_markdown(html: str) -> str:
    """Best-effort HTML to markdown conversion.

    Strips boilerplate, preserves tables and headers.
    This is intentionally simple — no heavy dependencies.
    """
    text = html

    # Strip script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Convert headers
    for i in range(1, 7):
        text = re.sub(rf"<h{i}[^>]*>(.*?)</h{i}>", rf"\n{'#' * i} \1\n", text, flags=re.IGNORECASE | re.DOTALL)

    # Convert paragraphs
    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)

    # Convert line breaks
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Convert bold/italic
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)

    # Convert list items
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)

    # Simple table conversion (best-effort)
    text = re.sub(r"<tr[^>]*>", "\n| ", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", " |", text, flags=re.IGNORECASE)
    text = re.sub(r"<t[dh][^>]*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</t[dh]>", " | ", text, flags=re.IGNORECASE)

    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")

    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()
