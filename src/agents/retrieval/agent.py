"""Retrieval Agent – routes queries to the correct data source adapters.

The retrieval agent is the single interface for all data fetching.  It
maintains a registry of adapters, selects the right one(s) for each
request, and merges results into a unified RetrievalResult.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.retrieval.base_adapter import BaseAdapter
from src.agents.retrieval.adapters.yahoo_finance import YahooFinanceAdapter
from src.agents.retrieval.adapters.fred import FREDAdapter
from src.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
from src.cache.cache_manager import CacheManager
from src.models.responses import RetrievalResult

logger = logging.getLogger(__name__)


class RetrievalAgent:
    """Orchestrates data fetching across multiple source adapters."""

    def __init__(
        self,
        cache: CacheManager | None = None,
        fred_api_key: str = "",
        edgar_identity: str = "",
    ) -> None:
        self._cache = cache
        self._adapters: dict[str, BaseAdapter] = {}

        # Register built-in adapters
        self.register_adapter(YahooFinanceAdapter(cache=cache))
        if fred_api_key:
            self.register_adapter(FREDAdapter(api_key=fred_api_key, cache=cache))
        if edgar_identity:
            self.register_adapter(SECEdgarAdapter(identity=edgar_identity, cache=cache))

    def register_adapter(self, adapter: BaseAdapter) -> None:
        """Add an adapter to the registry."""
        self._adapters[adapter.SOURCE_NAME] = adapter
        logger.info("Registered adapter: %s (%s)", adapter.SOURCE_NAME, adapter.SUPPORTED_CATEGORIES)

    @property
    def available_sources(self) -> list[str]:
        return list(self._adapters.keys())

    @property
    def available_categories(self) -> dict[str, list[str]]:
        return {name: adapter.SUPPORTED_CATEGORIES for name, adapter in self._adapters.items()}

    def fetch(self, tasks: list[dict[str, Any]]) -> RetrievalResult:
        """Execute one or more fetch tasks and merge results.

        Parameters
        ----------
        tasks : list of dict
            Each task has at least ``source`` and source-specific params.
            Example:
                [
                    {"source": "yahoo_finance", "ticker": "NVDA", "action": "quote"},
                    {"source": "fred", "series_id": "FEDFUNDS"},
                ]

        Returns
        -------
        RetrievalResult
            Merged result from all adapters.
        """
        all_data: list[dict[str, Any]] = []
        all_sources: list[str] = []
        all_errors: list[str] = []
        all_warnings: list[str] = []
        any_cached = False

        for task in tasks:
            source = task.get("source", "")
            adapter = self._adapters.get(source)

            if adapter is None:
                # Try to find by category
                adapter = self._find_adapter_for_task(task)

            if adapter is None:
                all_errors.append(
                    f"No adapter found for source={source!r}. "
                    f"Available: {self.available_sources}"
                )
                continue

            logger.info("Dispatching to %s: %s", adapter.SOURCE_NAME, task)
            result = adapter.fetch(task)

            all_data.extend(result.data)
            all_sources.extend(result.sources_used)
            all_errors.extend(result.errors)
            all_warnings.extend(result.warnings)
            if result.cached:
                any_cached = True

        return RetrievalResult(
            success=len(all_errors) == 0 or len(all_data) > 0,
            data=all_data,
            sources_used=list(set(all_sources)),
            cached=any_cached,
            errors=all_errors,
            warnings=all_warnings,
        )

    def _find_adapter_for_task(self, task: dict[str, Any]) -> BaseAdapter | None:
        """Heuristic: pick an adapter based on task keys."""
        # If there's a ticker and no explicit source, try Yahoo Finance
        if "ticker" in task and "series_id" not in task:
            action = task.get("action", "quote")
            if action in ("filings", "company_facts") or task.get("source") == "sec_edgar":
                return self._adapters.get("sec_edgar")
            return self._adapters.get("yahoo_finance")

        # If there's a series_id, it's FRED
        if "series_id" in task:
            return self._adapters.get("fred")

        return None

    def fetch_quote(self, ticker: str) -> RetrievalResult:
        """Convenience: fetch a stock quote."""
        return self.fetch([{"source": "yahoo_finance", "ticker": ticker, "action": "quote"}])

    def fetch_history(self, ticker: str, period: str = "1y", interval: str = "1d") -> RetrievalResult:
        """Convenience: fetch historical prices."""
        return self.fetch([{
            "source": "yahoo_finance",
            "ticker": ticker,
            "action": "history",
            "period": period,
            "interval": interval,
        }])

    def fetch_financials(self, ticker: str, statement_type: str = "income", quarterly: bool = False) -> RetrievalResult:
        """Convenience: fetch financial statements from Yahoo."""
        return self.fetch([{
            "source": "yahoo_finance",
            "ticker": ticker,
            "action": "financials",
            "statement_type": statement_type,
            "quarterly": quarterly,
        }])

    def fetch_macro(self, series_id: str) -> RetrievalResult:
        """Convenience: fetch a FRED series."""
        return self.fetch([{"source": "fred", "series_id": series_id}])

    def fetch_sec_filings(self, ticker: str, form: str | None = None, limit: int = 10) -> RetrievalResult:
        """Convenience: list SEC filings."""
        task: dict[str, Any] = {
            "source": "sec_edgar",
            "ticker": ticker,
            "action": "filings",
            "limit": limit,
        }
        if form:
            task["form"] = form
        return self.fetch([task])
