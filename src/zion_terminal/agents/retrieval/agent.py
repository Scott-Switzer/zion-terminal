"""Retrieval Agent – routes queries to the correct data source adapters."""

from __future__ import annotations

import logging
from typing import Any

from zion_terminal.agents.retrieval.base_adapter import BaseAdapter
from zion_terminal.agents.retrieval.adapters.yahoo_finance import YahooFinanceAdapter
from zion_terminal.agents.retrieval.adapters.fred import FREDAdapter
from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
from zion_terminal.cache.cache_manager import CacheManager
from zion_terminal.models.responses import RetrievalResult

logger = logging.getLogger(__name__)


class RetrievalAgent:
    def __init__(
        self,
        cache: CacheManager | None = None,
        fred_api_key: str = "",
        edgar_identity: str = "",
    ) -> None:
        self._cache = cache
        self._adapters: dict[str, BaseAdapter] = {}

        self.register_adapter(YahooFinanceAdapter(cache=cache))
        if fred_api_key:
            self.register_adapter(FREDAdapter(api_key=fred_api_key, cache=cache))
        if edgar_identity:
            self.register_adapter(SECEdgarAdapter(identity=edgar_identity, cache=cache))

    def register_adapter(self, adapter: BaseAdapter) -> None:
        self._adapters[adapter.SOURCE_NAME] = adapter

    @property
    def available_sources(self) -> list[str]:
        return list(self._adapters.keys())

    @property
    def available_categories(self) -> dict[str, list[str]]:
        return {name: adapter.SUPPORTED_CATEGORIES for name, adapter in self._adapters.items()}

    def fetch(self, tasks: list[dict[str, Any]]) -> RetrievalResult:
        all_data: list[dict[str, Any]] = []
        all_sources: list[str] = []
        all_errors: list[str] = []
        all_warnings: list[str] = []
        any_cached = False

        for task in tasks:
            source = task.get("source", "")
            adapter = self._adapters.get(source) or self._find_adapter_for_task(task)

            if adapter is None:
                all_errors.append(f"No adapter found for source={source!r}. Available: {self.available_sources}")
                continue

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
        if "ticker" in task and "series_id" not in task:
            action = task.get("action", "quote")
            # SEC-first: financials and filing-related actions go to SEC EDGAR
            if action in ("filings", "company_facts", "filing_markdown", "financials"):
                return self._adapters.get("sec_edgar") or self._adapters.get("yahoo_finance")
            return self._adapters.get("yahoo_finance")
        if "series_id" in task:
            return self._adapters.get("fred")
        return None

    # ── Convenience methods ──────────────────────────────────────
    def fetch_quote(self, ticker: str) -> RetrievalResult:
        return self.fetch([{"source": "yahoo_finance", "ticker": ticker, "action": "quote"}])

    def fetch_history(self, ticker: str, period: str = "1y", interval: str = "1d") -> RetrievalResult:
        return self.fetch([{"source": "yahoo_finance", "ticker": ticker, "action": "history", "period": period, "interval": interval}])

    def fetch_financials(self, ticker: str, statement_type: str = "income", quarterly: bool = False) -> RetrievalResult:
        """Fetch financials. Routes to SEC EDGAR (primary) or Yahoo (fallback)."""
        source = "sec_edgar" if "sec_edgar" in self._adapters else "yahoo_finance"
        return self.fetch([{"source": source, "ticker": ticker, "action": "financials", "statement_type": statement_type, "quarterly": quarterly}])

    def fetch_macro(self, series_id: str) -> RetrievalResult:
        return self.fetch([{"source": "fred", "series_id": series_id}])

    def fetch_sec_filings(self, ticker: str, form: str | None = None, limit: int = 10) -> RetrievalResult:
        task: dict[str, Any] = {"source": "sec_edgar", "ticker": ticker, "action": "filings", "limit": limit}
        if form:
            task["form"] = form
        return self.fetch([task])
