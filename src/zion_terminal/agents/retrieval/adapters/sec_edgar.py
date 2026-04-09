"""SEC EDGAR adapter — edgartools + direct SEC client.

Status: working. Requires EDGAR_IDENTITY (name + email) per SEC policy.
Rate limit: SEC enforces ~10 req/sec.

Architecture:
  - Direct SEC client (sec/client.py) handles: ticker→CIK resolution,
    filing discovery with year/quarter/date filtering, company facts JSON.
  - edgartools handles: financial statement parsing (filing.obj().financials),
    filing HTML extraction for the markdown pipeline.
  - When edgartools Company(ticker) fails, falls back to direct SEC client
    for CIK resolution.

Filing-to-markdown conversion uses the shared FilingPipeline
(pipeline/filing_pipeline.py), not a private converter.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import pandas as pd

from zion_terminal.agents.retrieval.base_adapter import BaseAdapter
from zion_terminal.agents.retrieval.retry import adapter_retry
from zion_terminal.models.financial import FilingType, SECFiling
from zion_terminal.models.responses import RetrievalResult
from zion_terminal.pipeline.filing_pipeline import FilingPipeline
from zion_terminal.sec.client import SECClient

logger = logging.getLogger(__name__)

_FORM_MAP: dict[str, FilingType] = {
    "10-K": FilingType.TEN_K, "10-Q": FilingType.TEN_Q,
    "8-K": FilingType.EIGHT_K, "DEF 14A": FilingType.PROXY,
}

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
        self._pipeline = FilingPipeline()
        self._sec_client = SECClient(identity=identity)

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

    @adapter_retry
    def _fetch_filings(self, ticker: str, params: dict) -> RetrievalResult:
        """Fetch filing list from SEC.

        Uses the direct SEC client when year/quarter/date filters are specified
        (edgartools doesn't support date-range filtering). Falls back to
        edgartools for unfiltered latest-N queries.

        Supports: form, limit, year, quarter, date_from, date_to.
        """
        form_filter = params.get("form")
        limit = params.get("limit", 10)
        year = params.get("year")
        quarter = params.get("quarter")
        date_from = params.get("date_from")
        date_to = params.get("date_to")

        # Use direct SEC client when date filters are needed
        if year or quarter or date_from or date_to:
            return self._fetch_filings_direct(ticker, form_filter, limit, year, quarter, date_from, date_to)

        # Default: use edgartools for simple latest-N queries
        from edgar import Company
        _rate_limit()

        try:
            company = Company(ticker)
        except Exception as exc:
            logger.warning("edgartools Company(%s) failed: %s — trying direct SEC client", ticker, exc)
            return self._fetch_filings_direct(ticker, form_filter, limit, year, quarter, date_from, date_to)

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

    def _fetch_filings_direct(
        self, ticker: str, form: str | None, limit: int,
        year: int | None, quarter: int | None,
        date_from: str | None, date_to: str | None,
    ) -> RetrievalResult:
        """Fetch filings via direct SEC client with date filtering."""
        filings = self._sec_client.get_filings(
            ticker, form=form, year=year, quarter=quarter,
            date_from=date_from, date_to=date_to, limit=limit,
        )
        if not filings:
            return RetrievalResult(
                success=False,
                errors=[f"No filings found for {ticker} with the specified filters"],
            )

        results: list[dict[str, Any]] = []
        for f in filings:
            filing_type = _FORM_MAP.get(f.get("form", ""), FilingType.OTHER)
            filing_date_val = None
            try:
                filing_date_val = date.fromisoformat(f["filingDate"])
            except (ValueError, KeyError):
                pass

            cik = f.get("cik", "")
            accession = f.get("accessionNumber", "")
            primary_doc = f.get("primaryDocument", "")
            doc_url = self._sec_client.build_filing_url(cik, accession, primary_doc) if primary_doc else ""

            sec_filing = SECFiling(
                ticker=ticker.upper(),
                company_name=f.get("companyName", ticker),
                cik=cik,
                filing_type=filing_type,
                filing_date=filing_date_val,
                accession_number=accession,
                document_url=doc_url,
                description=f"{f.get('form', '')} filing",
                source=self.SOURCE_NAME,
            )
            results.append(sec_filing.model_dump())

        return RetrievalResult(data=results, sources_used=[self.SOURCE_NAME])

    # Map user-facing statement type names to edgartools attribute names
    _STMT_ATTR_MAP: dict[str, str] = {
        "income": "income_statement",
        "income_statement": "income_statement",
        "balance": "balance_sheet",
        "balance_sheet": "balance_sheet",
        "cash_flow": "cash_flow_statement",
        "cash_flow_statement": "cash_flow_statement",
    }

    @adapter_retry
    def _fetch_financials(self, ticker: str, params: dict) -> RetrievalResult:
        """Fetch financial statements from SEC EDGAR.

        Honors:
          - statement_type: "income" | "balance" | "cash_flow" (default: all)
          - quarterly: True → 10-Q filings, False → 10-K filings
          - year: filter to specific fiscal year
          - quarter: filter to specific fiscal quarter (1-4)
          - limit: max number of filings to process (default 3)

        Uses edgartools for financial statement parsing. If year/quarter filters
        are specified, uses the direct SEC client for filing discovery first.
        """
        from edgar import Company
        _rate_limit()

        try:
            company = Company(ticker)
        except Exception as exc:
            logger.warning("edgartools Company(%s) failed: %s", ticker, exc)
            return RetrievalResult(success=False, errors=[f"Could not resolve {ticker} via edgartools: {exc}"])

        quarterly = params.get("quarterly", False)
        form = "10-Q" if quarterly else "10-K"
        year = params.get("year")
        quarter_filter = params.get("quarter")

        # If year/quarter specified, use direct SEC client to find matching filings
        # then use edgartools to parse financials from those filings
        if year or quarter_filter:
            direct_filings = self._sec_client.get_filings(
                ticker, form=form, year=year, quarter=quarter_filter, limit=10,
            )
            if not direct_filings:
                return RetrievalResult(
                    success=False,
                    errors=[f"No {form} filings found for {ticker} in year={year} quarter={quarter_filter}"],
                )
            # Use edgartools to process just these filings by matching accession numbers
            filings = company.get_filings(form=form)
            target_accessions = {f["accessionNumber"] for f in direct_filings}
        else:
            filings = company.get_filings(form=form)
            target_accessions = None

        limit = params.get("limit", 3)
        requested_type = params.get("statement_type", "")

        # Determine which statements to extract
        if requested_type and requested_type in self._STMT_ATTR_MAP:
            stmt_attrs = [self._STMT_ATTR_MAP[requested_type]]
        else:
            # No specific type requested — return all three
            stmt_attrs = ["income_statement", "balance_sheet", "cash_flow_statement"]

        results: list[dict[str, Any]] = []

        for i, filing in enumerate(filings):
            if i >= limit:
                break
            # If filtering by accession, skip non-matching filings
            if target_accessions is not None:
                accession = str(getattr(filing, "accession_no", ""))
                if accession not in target_accessions:
                    limit += 1  # Don't count skipped filings against limit
                    continue
            _rate_limit()
            try:
                filing_obj = filing.obj()
                if hasattr(filing_obj, "financials") and filing_obj.financials is not None:
                    for stmt_name in stmt_attrs:
                        stmt = getattr(filing_obj.financials, stmt_name, None)
                        if stmt is not None:
                            try:
                                df = stmt.to_dataframe() if hasattr(stmt, "to_dataframe") else None
                                if df is not None and not df.empty:
                                    # Convert DataFrame to line_items dict for formatter compatibility
                                    # Use the first (most recent) column as values
                                    line_items = {}
                                    for idx in df.index:
                                        val = df.iloc[:, 0].loc[idx] if len(df.columns) > 0 else None
                                        line_items[str(idx)] = float(val) if pd.notna(val) else None
                                    results.append({
                                        "ticker": ticker,
                                        "statement_type": stmt_name,
                                        "period": str(df.columns[0]) if len(df.columns) > 0 else "",
                                        "line_items": line_items,
                                        "filing_date": str(getattr(filing, "filing_date", "")),
                                        "form": form,
                                        "source": self.SOURCE_NAME,
                                    })
                            except Exception:
                                results.append({
                                    "ticker": ticker,
                                    "statement_type": stmt_name,
                                    "period": str(getattr(filing, "filing_date", "")),
                                    "line_items": {},
                                    "filing_date": str(getattr(filing, "filing_date", "")),
                                    "form": form,
                                    "raw_text": str(stmt)[:2000],
                                    "source": self.SOURCE_NAME,
                                })
            except Exception as exc:
                logger.warning("Could not parse filing %d for %s: %s", i, ticker, exc)
                continue

        if not results:
            return RetrievalResult(success=False, errors=[f"No parseable financial data found for {ticker} ({form})"])
        return RetrievalResult(data=results, sources_used=[self.SOURCE_NAME])

    @adapter_retry
    def _fetch_company_facts(self, ticker: str, params: dict) -> RetrievalResult:
        """Fetch XBRL company facts from SEC EDGAR.

        Supports:
          - namespace: filter by taxonomy namespace (e.g. "us-gaap", "dei")
          - concept: filter by concept name substring
          - limit: max facts to return (default 100, 0 = all)
          - offset: pagination offset (default 0)
        """
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

        namespace_filter = params.get("namespace", "").lower()
        concept_filter = params.get("concept", "").lower()
        limit = params.get("limit", 100)
        offset = params.get("offset", 0)

        if facts is not None:
            try:
                df = facts.to_pandas() if hasattr(facts, "to_pandas") else None
                if df is not None and not df.empty:
                    facts_data["total_facts"] = len(df)

                    # Apply namespace filter
                    filtered = df
                    if namespace_filter:
                        ns_cols = [c for c in df.columns if "namespace" in c.lower() or "taxonomy" in c.lower()]
                        if ns_cols:
                            filtered = filtered[filtered[ns_cols[0]].str.lower().str.contains(namespace_filter, na=False)]

                    # Apply concept filter
                    if concept_filter:
                        concept_cols = [c for c in filtered.columns if "concept" in c.lower() or "label" in c.lower() or "name" in c.lower()]
                        if concept_cols:
                            mask = filtered[concept_cols[0]].str.lower().str.contains(concept_filter, na=False)
                            filtered = filtered[mask]

                    facts_data["filtered_facts"] = len(filtered)

                    # Pagination
                    page = filtered.iloc[offset:offset + limit] if limit > 0 else filtered.iloc[offset:]
                    facts_data["facts_count"] = len(page)
                    facts_data["offset"] = offset
                    facts_data["limit"] = limit
                    facts_data["has_more"] = (offset + len(page)) < len(filtered)
                    facts_data["sample_facts"] = page.to_dict(orient="records")

                    # Group by namespace/category for overview
                    ns_cols = [c for c in df.columns if "namespace" in c.lower() or "taxonomy" in c.lower()]
                    if ns_cols:
                        groups = df[ns_cols[0]].value_counts().to_dict()
                        facts_data["namespace_summary"] = {str(k): int(v) for k, v in groups.items()}
                else:
                    facts_data["facts_summary"] = str(facts)[:3000]
            except Exception:
                facts_data["facts_summary"] = str(facts)[:3000]

        return RetrievalResult(data=[facts_data], sources_used=[self.SOURCE_NAME])

    @adapter_retry
    def _fetch_filing_markdown(self, ticker: str, params: dict) -> RetrievalResult:
        """Fetch a filing and process through the unified pipeline.

        Uses the shared FilingPipeline (converter + segmenter) instead of
        a private conversion path. This is the single live filing path.
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
            filing_obj = filing.obj()
            html_content = None

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

            # Run through the unified filing pipeline
            filing_date_str = str(getattr(filing, "filing_date", ""))
            pipeline_result = self._pipeline.process(
                html=html_content,
                ticker=ticker,
                form=form,
                filing_date=filing_date_str,
                metadata={"ticker": ticker, "form": form, "source": self.SOURCE_NAME},
            )

            if not pipeline_result.success:
                return RetrievalResult(
                    success=False,
                    errors=pipeline_result.errors,
                )

            filing_data = SECFiling(
                ticker=ticker,
                company_name=str(getattr(company, "name", ticker)),
                cik=str(getattr(company, "cik", "")),
                filing_type=_FORM_MAP.get(form, FilingType.OTHER),
                filing_date=filing.filing_date if isinstance(getattr(filing, "filing_date", None), date) else None,
                accession_number=str(getattr(filing, "accession_no", "")),
                document_url=str(getattr(filing, "homepage_url", "")),
                description=f"{form} filing (pipeline conversion)",
                content_markdown=pipeline_result.markdown[:100_000],
                source=self.SOURCE_NAME,
            )

            result_data = filing_data.model_dump()
            result_data["pipeline_metadata"] = pipeline_result.to_dict()

            return RetrievalResult(data=[result_data], sources_used=[self.SOURCE_NAME])

        except Exception as exc:
            logger.warning("Filing pipeline failed for %s: %s", ticker, exc)
            return RetrievalResult(
                success=False,
                errors=[f"Filing pipeline failed: {exc}"],
            )

