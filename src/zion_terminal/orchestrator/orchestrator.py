"""Orchestrator – single entry point for all Zion Terminal requests.

Works without any LLM provider. The LLM is only used for:
  - Ambiguous query parsing (fallback)
  - Synthetic press release generation (optional)
All core retrieval works with LLM_PROVIDER=none.

Public methods:
  query()            – NL query (uses intent parser)
  get_quote()        – direct stock quote
  get_history()      – direct historical data
  get_financials()   – direct financial statements
  get_filings()      – direct SEC filings list
  get_macro()        – direct FRED macro data
  get_info()         – direct company info
  get_filing_markdown() – fetch filing as markdown (experimental)
  get_company_facts()   – fetch XBRL company facts

All public methods route through validation. CLI must use these
instead of reaching into private agents.
"""

from __future__ import annotations

import logging
from typing import Any

from zion_terminal.agents.retrieval.agent import RetrievalAgent
from zion_terminal.agents.validation.agent import ValidationAgent
from zion_terminal.agents.synthesis.agent import SynthesisAgent
from zion_terminal.cache.cache_manager import CacheManager
from zion_terminal.models.responses import (
    AgentResponse, OrchestratorResponse, RetrievalResult, ValidationResult,
)
from zion_terminal.orchestrator.intent_parser import IntentParser, ParsedIntent
from zion_terminal.providers.base import BaseLLMProvider, NoLLMProvider

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        fred_api_key: str = "",
        edgar_identity: str = "",
        llm: BaseLLMProvider | None = None,
        cache_dir: str = ".cache/zion",
        cache_ttl: int = 3600,
        strict: bool = False,
    ) -> None:
        self._cache = CacheManager(cache_dir=cache_dir, default_ttl=cache_ttl)
        self._llm = llm or NoLLMProvider()
        self._strict = strict

        self._retrieval = RetrievalAgent(
            cache=self._cache,
            fred_api_key=fred_api_key,
            edgar_identity=edgar_identity,
        )
        self._validation = ValidationAgent()
        self._synthesis = SynthesisAgent(llm=self._llm)
        self._parser = IntentParser(llm=self._llm)

        logger.info("Orchestrator ready — sources: %s, LLM: %s, strict: %s",
                     self._retrieval.available_sources, self._llm.name, self._strict)

    @property
    def available_sources(self) -> list[str]:
        return self._retrieval.available_sources

    # ── Natural-language entry point ────────────────────────────────

    def query(self, text: str, validate: bool = True) -> OrchestratorResponse:
        logger.info("Processing: %s", text[:120])
        parsed: ParsedIntent = self._parser.parse(text)

        if not parsed.tasks:
            return OrchestratorResponse(
                success=False, query=text, intent=parsed.intent,
                errors=["Could not understand the query. Try mentioning a ticker (e.g. AAPL) or macro indicator (e.g. GDP)."],
            )

        if parsed.needs_synthesis:
            return self._handle_synthesis(parsed)

        meta: dict[str, Any] = {
            "tickers": parsed.tickers,
            "macro_series": parsed.macro_series,
            "tasks_count": len(parsed.tasks),
            "llm_provider": self._llm.name,
        }
        if parsed.params.get("_llm_assisted"):
            meta["llm_assisted"] = True

        return self._fetch_and_validate(
            tasks=parsed.tasks,
            query=text,
            intent=parsed.intent,
            validate=validate,
            metadata=meta,
        )

    # ── Public direct-access methods ────────────────────────────────
    # CLI and programmatic consumers MUST use these instead of
    # reaching into self._retrieval directly.

    def get_quote(self, ticker: str, *, validate: bool = True) -> OrchestratorResponse:
        return self._fetch_and_validate(
            tasks=[{"source": "yahoo_finance", "ticker": ticker.upper(), "action": "quote"}],
            query=f"{ticker} quote",
            intent="quote",
            validate=validate,
        )

    def get_history(
        self, ticker: str, *, period: str = "1y", interval: str = "1d", validate: bool = True,
    ) -> OrchestratorResponse:
        return self._fetch_and_validate(
            tasks=[{"source": "yahoo_finance", "ticker": ticker.upper(), "action": "history",
                    "period": period, "interval": interval}],
            query=f"{ticker} history {period} {interval}",
            intent="history",
            validate=validate,
        )

    def get_financials(
        self, ticker: str, *, statement_type: str = "income",
        quarterly: bool = False, validate: bool = True,
        source: str = "sec", year: int | None = None, quarter: int | None = None,
    ) -> OrchestratorResponse:
        """Get financial statements. SEC is the primary source.

        Args:
            source: "sec" (default, primary) or "yahoo" (verification/fallback)
            year: Filter to specific fiscal year (e.g. 2022)
            quarter: Filter to specific fiscal quarter (1-4)
        """
        if source == "sec" and "sec_edgar" in self._retrieval.available_sources:
            task: dict[str, Any] = {
                "source": "sec_edgar", "ticker": ticker.upper(),
                "action": "financials",
                "statement_type": statement_type, "quarterly": quarterly,
            }
            if year:
                task["year"] = year
            if quarter:
                task["quarter"] = quarter
            tasks = [task]
        else:
            # Yahoo Finance fallback — does not support year/quarter
            tasks = [{
                "source": "yahoo_finance", "ticker": ticker.upper(),
                "action": "financials",
                "statement_type": statement_type, "quarterly": quarterly,
            }]
        return self._fetch_and_validate(
            tasks=tasks,
            query=f"{ticker} {statement_type} {'quarterly' if quarterly else 'annual'}",
            intent="financials",
            validate=validate,
            metadata={"source_preference": source, "source_role": "primary" if source == "sec" else "fallback"},
        )

    def get_filings(
        self, ticker: str, *, form: str | None = None, limit: int = 10,
        year: int | None = None, quarter: int | None = None,
        validate: bool = True,
    ) -> OrchestratorResponse:
        """Get SEC filings list with optional date filters."""
        task: dict[str, Any] = {
            "source": "sec_edgar", "ticker": ticker.upper(),
            "action": "filings", "limit": limit,
        }
        if form:
            task["form"] = form
        if year:
            task["year"] = year
        if quarter:
            task["quarter"] = quarter
        return self._fetch_and_validate(
            tasks=[task],
            query=f"{ticker} filings form={form} limit={limit}",
            intent="filings",
            validate=validate,
        )

    def get_macro(
        self, series_id: str, *, start_date: str | None = None,
        end_date: str | None = None, validate: bool = True,
    ) -> OrchestratorResponse:
        task: dict[str, Any] = {"source": "fred", "series_id": series_id.upper()}
        if start_date:
            task["start_date"] = start_date
        if end_date:
            task["end_date"] = end_date
        return self._fetch_and_validate(
            tasks=[task],
            query=f"FRED {series_id}",
            intent="macro",
            validate=validate,
        )

    def get_info(self, ticker: str, *, validate: bool = True) -> OrchestratorResponse:
        return self._fetch_and_validate(
            tasks=[{"source": "yahoo_finance", "ticker": ticker.upper(), "action": "info"}],
            query=f"{ticker} company info",
            intent="company_info",
            validate=validate,
        )

    def get_filing_markdown(
        self, ticker: str, *, form: str = "10-K", year: int | None = None,
        validate: bool = True,
    ) -> OrchestratorResponse:
        """Fetch an SEC filing and convert to markdown (experimental).

        Args:
            year: If specified, fetch filing from that year instead of latest.
        """
        task: dict[str, Any] = {
            "source": "sec_edgar", "ticker": ticker.upper(),
            "action": "filing_markdown", "form": form,
        }
        if year:
            task["year"] = year
        return self._fetch_and_validate(
            tasks=[task],
            query=f"{ticker} {form} filing markdown",
            intent="filing_markdown",
            validate=validate,
        )

    def get_company_facts(
        self, ticker: str, *, validate: bool = True,
    ) -> OrchestratorResponse:
        """Fetch XBRL company facts from SEC EDGAR."""
        return self._fetch_and_validate(
            tasks=[{"source": "sec_edgar", "ticker": ticker.upper(),
                    "action": "company_facts"}],
            query=f"{ticker} company facts",
            intent="company_facts",
            validate=validate,
        )

    # ── Internal helpers ────────────────────────────────────────────

    def _fetch_and_validate(
        self,
        tasks: list[dict[str, Any]],
        query: str,
        intent: str,
        validate: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> OrchestratorResponse:
        """Shared retrieval + optional validation pipeline.

        In strict mode, validation failures cause the entire response to
        report ``success=False`` so callers can gate on data quality.
        """
        retrieval_result: RetrievalResult = self._retrieval.fetch(tasks)
        results: list[AgentResponse] = [retrieval_result]
        success = retrieval_result.success

        if validate and retrieval_result.success and retrieval_result.data:
            validation_result: ValidationResult = self._validation.validate_retrieval(
                retrieval_result
            )
            results.append(validation_result)

            if self._strict and not validation_result.success:
                success = False
                logger.warning(
                    "Strict mode: validation failed for query=%s (%d/%d checks failed)",
                    query, validation_result.checks_failed, validation_result.checks_run,
                )

        return OrchestratorResponse(
            success=success, query=query, intent=intent,
            results=results, errors=retrieval_result.errors,
            metadata=metadata or {},
        )

    def _handle_synthesis(self, parsed: ParsedIntent) -> OrchestratorResponse:
        result = self._synthesis.generate(parsed.raw_query, parsed.params)

        if result.success and result.documents:
            validation = self._validation.validate_synthesis(result)

            success = result.success
            if self._strict and not validation.success:
                success = False
                logger.warning("Strict mode: synthesis validation failed")

            return OrchestratorResponse(
                success=success, query=parsed.raw_query, intent="synthesis",
                results=[result, validation], errors=result.errors,
            )

        return OrchestratorResponse(
            success=result.success, query=parsed.raw_query, intent="synthesis",
            results=[result], errors=result.errors,
        )

    def close(self) -> None:
        self._cache.close()
