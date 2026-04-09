"""Direct SEC EDGAR API client.

Uses SEC's free, no-auth JSON APIs at data.sec.gov to provide reliable
ticker→CIK resolution, filing discovery with date/year/quarter filtering,
and company facts retrieval.

This supplements edgartools — edgartools is still used for financial
statement parsing (filing.obj().financials), but this client handles
the filing discovery and metadata layers where edgartools can be brittle.

SEC API documentation: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
Rate limit: 10 requests/second per IP. User-Agent header required.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Rate limiter shared with the edgartools adapter
_last_request_time: float = 0.0
_MIN_REQUEST_INTERVAL: float = 0.12  # ~8 req/sec to stay under SEC's 10/sec

_BASE = "https://data.sec.gov"
_SEC_BASE = "https://www.sec.gov"

# In-memory cache for ticker→CIK map (loaded once per process)
_ticker_map: dict[str, dict[str, Any]] | None = None


def _rate_limit() -> None:
    """Enforce SEC rate limit."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()


class SECClient:
    """Direct client for SEC EDGAR JSON APIs.

    Does not depend on edgartools. Uses only requests + SEC public endpoints.
    """

    def __init__(self, identity: str = "") -> None:
        """Initialize with SEC identity string (name + email)."""
        self._identity = identity or "ZionTerminal admin@example.com"
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._identity,
            "Accept": "application/json",
        })

    def resolve_cik(self, ticker: str) -> str | None:
        """Resolve a stock ticker to a 10-digit zero-padded CIK.

        Uses SEC's company_tickers.json endpoint, cached in memory.
        Returns None if ticker is not found.
        """
        global _ticker_map
        if _ticker_map is None:
            _ticker_map = self._load_ticker_map()

        upper = ticker.upper().replace(".", "-")  # BRK.B → BRK-B
        entry = _ticker_map.get(upper)
        if entry:
            return str(entry["cik_str"]).zfill(10)
        return None

    def _load_ticker_map(self) -> dict[str, dict[str, Any]]:
        """Load the SEC ticker→CIK mapping table."""
        _rate_limit()
        try:
            resp = self._session.get(
                f"{_SEC_BASE}/files/company_tickers.json", timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # Build ticker-keyed lookup
            result: dict[str, dict[str, Any]] = {}
            for entry in data.values():
                ticker_key = str(entry.get("ticker", "")).upper()
                if ticker_key:
                    result[ticker_key] = entry
            logger.info("Loaded %d tickers from SEC", len(result))
            return result
        except Exception as exc:
            logger.warning("Failed to load SEC ticker map: %s", exc)
            return {}

    def get_submissions(self, cik: str) -> dict[str, Any] | None:
        """Fetch company submissions (filing history) from SEC.

        Returns the full JSON response including recent filings.
        CIK should be 10-digit zero-padded.
        """
        _rate_limit()
        try:
            resp = self._session.get(
                f"{_BASE}/submissions/CIK{cik}.json", timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            logger.warning("SEC submissions request failed for CIK %s: %s", cik, exc)
            return None
        except Exception as exc:
            logger.warning("SEC submissions error for CIK %s: %s", cik, exc)
            return None

    def get_filings(
        self,
        ticker: str,
        *,
        form: str | None = None,
        year: int | None = None,
        quarter: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get filing metadata with optional filters.

        Args:
            ticker: Stock ticker symbol
            form: Filter by form type (e.g. "10-K", "10-Q")
            year: Filter by filing year
            quarter: Filter by fiscal quarter (1-4)
            date_from: Filter filings on or after this date (YYYY-MM-DD)
            date_to: Filter filings on or before this date (YYYY-MM-DD)
            limit: Maximum number of filings to return

        Returns:
            List of filing metadata dicts with keys:
            accessionNumber, filingDate, form, primaryDocument, etc.
        """
        cik = self.resolve_cik(ticker)
        if not cik:
            logger.warning("Could not resolve CIK for ticker %s", ticker)
            return []

        submissions = self.get_submissions(cik)
        if not submissions:
            return []

        recent = submissions.get("filings", {}).get("recent", {})
        if not recent:
            return []

        # Build list of filings from columnar data
        count = len(recent.get("accessionNumber", []))
        filings: list[dict[str, Any]] = []
        for i in range(count):
            filing = {
                "accessionNumber": recent["accessionNumber"][i],
                "filingDate": recent["filingDate"][i],
                "form": recent["form"][i],
                "primaryDocument": recent.get("primaryDocument", [""])[i] if i < len(recent.get("primaryDocument", [])) else "",
                "primaryDocDescription": recent.get("primaryDocDescription", [""])[i] if i < len(recent.get("primaryDocDescription", [])) else "",
                "cik": cik,
                "ticker": ticker.upper(),
                "companyName": submissions.get("name", ""),
            }
            filings.append(filing)

        # Apply filters
        filtered = filings
        if form:
            filtered = [f for f in filtered if f["form"] == form]
        if year:
            filtered = [f for f in filtered if f["filingDate"][:4] == str(year)]
        if quarter:
            filtered = [f for f in filtered if _filing_in_quarter(f["filingDate"], quarter)]
        if date_from:
            filtered = [f for f in filtered if f["filingDate"] >= date_from]
        if date_to:
            filtered = [f for f in filtered if f["filingDate"] <= date_to]

        return filtered[:limit]

    def get_company_facts(self, ticker: str) -> dict[str, Any] | None:
        """Fetch all XBRL company facts from SEC.

        Returns the full companyfacts JSON for the company.
        """
        cik = self.resolve_cik(ticker)
        if not cik:
            return None

        _rate_limit()
        try:
            resp = self._session.get(
                f"{_BASE}/api/xbrl/companyfacts/CIK{cik}.json", timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("SEC company facts error for %s: %s", ticker, exc)
            return None

    def build_filing_url(self, cik: str, accession_number: str, primary_document: str) -> str:
        """Construct the full URL for a filing document.

        Args:
            cik: 10-digit CIK (with leading zeros)
            accession_number: e.g. "0000320193-24-000123"
            primary_document: e.g. "aapl-20240928.htm"
        """
        accession_no_dashes = accession_number.replace("-", "")
        return f"{_SEC_BASE}/Archives/edgar/data/{cik.lstrip('0') or '0'}/{accession_no_dashes}/{primary_document}"


def _filing_in_quarter(filing_date: str, quarter: int) -> bool:
    """Check if a filing date falls in the given calendar quarter."""
    try:
        month = int(filing_date[5:7])
        q = (month - 1) // 3 + 1
        return q == quarter
    except (ValueError, IndexError):
        return False
