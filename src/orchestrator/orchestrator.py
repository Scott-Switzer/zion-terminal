"""Orchestrator – the single entry point for all Zion Terminal requests.

Parses incoming queries, determines which agents to invoke, manages
coordination for multi-step tasks, and returns a unified response.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.retrieval.agent import RetrievalAgent
from src.agents.validation.agent import ValidationAgent
from src.agents.synthesis.agent import SynthesisAgent
from src.cache.cache_manager import CacheManager
from src.models.responses import (
    OrchestratorResponse,
    RetrievalResult,
    ValidationResult,
)
from src.orchestrator.intent_parser import IntentParser, ParsedIntent

logger = logging.getLogger(__name__)


class Orchestrator:
    """Central coordinator: parse → route → fetch → validate → respond."""

    def __init__(
        self,
        fred_api_key: str = "",
        edgar_identity: str = "",
        openai_api_key: str = "",
        openai_model: str = "gpt-4o-mini",
        cache_dir: str = ".cache/zion",
        cache_ttl: int = 3600,
    ) -> None:
        # Initialise shared cache
        self._cache = CacheManager(cache_dir=cache_dir, default_ttl=cache_ttl)

        # Initialise optional OpenAI client for LLM-powered parsing
        self._openai_client = None
        if openai_api_key:
            try:
                from openai import OpenAI
                self._openai_client = OpenAI(api_key=openai_api_key)
            except ImportError:
                logger.warning("openai package not installed – LLM features disabled")

        # Initialise agents
        self._retrieval = RetrievalAgent(
            cache=self._cache,
            fred_api_key=fred_api_key,
            edgar_identity=edgar_identity,
        )
        self._validation = ValidationAgent()
        self._synthesis = SynthesisAgent(
            openai_client=self._openai_client,
            model=openai_model,
        )

        # Intent parser
        self._parser = IntentParser(
            openai_client=self._openai_client,
            model=openai_model,
        )

        logger.info(
            "Orchestrator initialised – sources: %s, LLM: %s",
            self._retrieval.available_sources,
            "enabled" if self._openai_client else "disabled",
        )

    @property
    def available_sources(self) -> list[str]:
        return self._retrieval.available_sources

    def query(self, text: str, validate: bool = True) -> OrchestratorResponse:
        """Process a natural-language query end-to-end.

        Parameters
        ----------
        text : str
            The user's query in plain English.
        validate : bool
            Whether to run validation on retrieved data.

        Returns
        -------
        OrchestratorResponse
        """
        logger.info("Processing query: %s", text[:120])
        errors: list[str] = []

        # 1. Parse intent
        parsed: ParsedIntent = self._parser.parse(text)

        if not parsed.tasks:
            return OrchestratorResponse(
                success=False,
                query=text,
                intent=parsed.intent,
                errors=["Could not understand the query. Try mentioning a ticker (e.g. AAPL) or macro indicator (e.g. GDP)."],
            )

        # 2. Handle synthesis requests separately
        if parsed.needs_synthesis:
            return self._handle_synthesis(parsed)

        # 3. Dispatch to retrieval agent
        retrieval_result: RetrievalResult = self._retrieval.fetch(parsed.tasks)

        results: list = [retrieval_result]

        # 4. Optional validation
        if validate and retrieval_result.success and retrieval_result.data:
            validation_result: ValidationResult = self._validation.validate_retrieval(
                retrieval_result
            )
            results.append(validation_result)
            if validation_result.warnings:
                errors.extend(
                    [f"Validation warning: {w}" for w in validation_result.warnings]
                )

        return OrchestratorResponse(
            success=retrieval_result.success,
            query=text,
            intent=parsed.intent,
            results=results,
            errors=retrieval_result.errors + errors,
            metadata={
                "tickers": parsed.tickers,
                "macro_series": parsed.macro_series,
                "tasks_count": len(parsed.tasks),
            },
        )

    def _handle_synthesis(self, parsed: ParsedIntent) -> OrchestratorResponse:
        """Handle synthetic entity generation requests."""
        result = self._synthesis.generate(parsed.raw_query, parsed.params)
        return OrchestratorResponse(
            success=result.success,
            query=parsed.raw_query,
            intent="synthesis",
            results=[result],
            errors=result.errors,
        )

    # ── Convenience methods ──────────────────────────────────────

    def get_quote(self, ticker: str) -> OrchestratorResponse:
        return self.query(f"Get current price for {ticker}")

    def get_financials(self, ticker: str, quarterly: bool = False) -> OrchestratorResponse:
        q = "quarterly" if quarterly else "annual"
        return self.query(f"Pull {ticker}'s {q} financials")

    def get_macro(self, indicator: str) -> OrchestratorResponse:
        return self.query(f"Get the current {indicator}")

    def close(self) -> None:
        """Clean up resources."""
        self._cache.close()
