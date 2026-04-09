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

    def _fetch_older_filings(
        self, cik: str, older_files: list[str],
    ) -> list[dict[str, Any]]:
        """Walk older filing archive files referenced in submissions JSON.

        The SEC submissions endpoint returns only the most recent ~1000
        filings in ``filings.recent``.  Older filings are paginated into
        separate JSON files listed in ``filings.files``.

        Each file has the same columnar structure as ``filings.recent``,
        including ``reportDate`` (the fiscal period end date).
        """
        all_filings: list[dict[str, Any]] = []
        for file_ref in older_files:
            filename = file_ref if isinstance(file_ref, str) else file_ref.get("name", "")
            if not filename:
                continue
            _rate_limit()
            try:
                resp = self._session.get(
                    f"{_BASE}/submissions/{filename}", timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                count = len(data.get("accessionNumber", []))
                for i in range(count):
                    filing = {
                        "accessionNumber": data["accessionNumber"][i],
                        "filingDate": data["filingDate"][i],
                        "reportDate": data.get("reportDate", [""])[i] if i < len(data.get("reportDate", [])) else "",
                        "form": data["form"][i],
                        "primaryDocument": data.get("primaryDocument", [""])[i] if i < len(data.get("primaryDocument", [])) else "",
                        "primaryDocDescription": data.get("primaryDocDescription", [""])[i] if i < len(data.get("primaryDocDescription", [])) else "",
                    }
                    all_filings.append(filing)
            except Exception as exc:
                logger.warning("Failed to fetch older filing file %s: %s", filename, exc)
                continue
        return all_filings

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
        include_archival: bool = True,
    ) -> list[dict[str, Any]]:
        """Get filing metadata with optional filters.

        Args:
            ticker: Stock ticker symbol
            form: Filter by form type (e.g. "10-K", "10-Q")
            year: Filter by fiscal year (uses ``reportDate``, not ``filingDate``)
            quarter: Filter by fiscal quarter (1-4, derived from ``reportDate``)
            date_from: Filter filings on or after this date (YYYY-MM-DD, uses filingDate)
            date_to: Filter filings on or before this date (YYYY-MM-DD, uses filingDate)
            limit: Maximum number of filings to return
            include_archival: Walk older filing files when recent filings
                don't satisfy the query.  Defaults to True.

        Returns:
            List of filing metadata dicts with keys:
            accessionNumber, filingDate, reportDate, form, primaryDocument, etc.

        Note:
            ``year`` and ``quarter`` filter on ``reportDate`` (the fiscal period
            end date), NOT ``filingDate`` (the date the filing was accepted by SEC).
            This is critical for fiscal correctness: a FY2023 10-K filed in
            January 2024 has reportDate=2023-09-30, filingDate=2024-01-05.
            Requesting year=2023 must return this filing.
        """
        cik = self.resolve_cik(ticker)
        if not cik:
            logger.warning("Could not resolve CIK for ticker %s", ticker)
            return []

        submissions = self.get_submissions(cik)
        if not submissions:
            return []

        company_name = submissions.get("name", "")
        recent = submissions.get("filings", {}).get("recent", {})
        if not recent:
            return []

        # Build list of filings from columnar (recent) data
        filings = self._columnar_to_filings(recent, cik, ticker, company_name)

        # Apply filters on recent filings first
        filtered = self._apply_filing_filters(
            filings, form=form, year=year, quarter=quarter,
            date_from=date_from, date_to=date_to,
        )

        # If recent filings don't satisfy the query, walk archival files
        if len(filtered) < limit and include_archival:
            older_files = submissions.get("filings", {}).get("files", [])
            if older_files:
                logger.info(
                    "Recent filings insufficient (%d/%d) — walking %d archival files for %s",
                    len(filtered), limit, len(older_files), ticker,
                )
                older_raw = self._fetch_older_filings(cik, older_files)
                # Enrich with CIK / ticker / company name
                for f in older_raw:
                    f["cik"] = cik
                    f["ticker"] = ticker.upper()
                    f["companyName"] = company_name
                older_filtered = self._apply_filing_filters(
                    older_raw, form=form, year=year, quarter=quarter,
                    date_from=date_from, date_to=date_to,
                )
                filtered.extend(older_filtered)

        return filtered[:limit]

    @staticmethod
    def _columnar_to_filings(
        columnar: dict[str, Any], cik: str, ticker: str, company_name: str,
    ) -> list[dict[str, Any]]:
        """Convert SEC's columnar filing data to a list of dicts.

        Includes ``reportDate`` (fiscal period end date) alongside
        ``filingDate`` (SEC acceptance date).
        """
        count = len(columnar.get("accessionNumber", []))
        filings: list[dict[str, Any]] = []
        for i in range(count):
            filing = {
                "accessionNumber": columnar["accessionNumber"][i],
                "filingDate": columnar["filingDate"][i],
                "reportDate": columnar.get("reportDate", [""])[i] if i < len(columnar.get("reportDate", [])) else "",
                "form": columnar["form"][i],
                "primaryDocument": columnar.get("primaryDocument", [""])[i] if i < len(columnar.get("primaryDocument", [])) else "",
                "primaryDocDescription": columnar.get("primaryDocDescription", [""])[i] if i < len(columnar.get("primaryDocDescription", [])) else "",
                "cik": cik,
                "ticker": ticker.upper(),
                "companyName": company_name,
            }
            filings.append(filing)
        return filings

    @staticmethod
    def _apply_filing_filters(
        filings: list[dict[str, Any]],
        *,
        form: str | None = None,
        year: int | None = None,
        quarter: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Apply form/year/quarter/date filters to a filing list.

        ``year`` and ``quarter`` filter on ``reportDate`` (the fiscal period
        end date from the filing, e.g. 2023-09-30 for Apple's FY2023 10-K).
        ``date_from`` and ``date_to`` filter on ``filingDate`` (the SEC
        acceptance date), which is appropriate for date-range queries.

        Falls back to ``filingDate`` for year/quarter only when ``reportDate``
        is missing or empty (legacy filings before SEC provided this field).
        """
        filtered = filings
        if form:
            filtered = [f for f in filtered if f["form"] == form]
        if year:
            filtered = [
                f for f in filtered
                if _filing_year(f) == year
            ]
        if quarter:
            filtered = [
                f for f in filtered
                if _filing_in_quarter_by_report_date(f, quarter)
            ]
        if date_from:
            filtered = [f for f in filtered if f["filingDate"] >= date_from]
        if date_to:
            filtered = [f for f in filtered if f["filingDate"] <= date_to]
        return filtered

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


def _filing_year(filing: dict[str, Any]) -> int | None:
    """Extract the fiscal year from a filing's reportDate.

    ``reportDate`` is the period-of-report date from SEC's submissions
    endpoint.  For 10-K filings it is the fiscal year end date; for 10-Q
    it is the quarter end date.  This is the semantically correct field
    for fiscal-year filtering.

    Falls back to ``filingDate`` when ``reportDate`` is missing (some very
    old filings or non-standard forms may not have it).
    """
    report_date = filing.get("reportDate", "")
    date_str = report_date if report_date else filing.get("filingDate", "")
    try:
        return int(date_str[:4])
    except (ValueError, IndexError):
        return None


def _filing_in_quarter_by_report_date(filing: dict[str, Any], quarter: int) -> bool:
    """Check if a filing's fiscal period falls in the given calendar quarter.

    Uses ``reportDate`` (period end date) to derive the quarter, falling
    back to ``filingDate`` when ``reportDate`` is unavailable.
    """
    report_date = filing.get("reportDate", "")
    date_str = report_date if report_date else filing.get("filingDate", "")
    try:
        month = int(date_str[5:7])
        q = (month - 1) // 3 + 1
        return q == quarter
    except (ValueError, IndexError):
        return False
