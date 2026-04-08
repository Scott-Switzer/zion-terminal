"""Orchestrator – single entry point for all Zion Terminal requests.

Works without any LLM provider. The LLM is only used for:
  - Ambiguous query parsing (fallback)
  - Synthetic press release generation (optional)
All core retrieval works with LLM_PROVIDER=none.
"""

from __future__ import annotations

import logging
from typing import Any

from zion_terminal.agents.retrieval.agent import RetrievalAgent
from zion_terminal.agents.validation.agent import ValidationAgent
from zion_terminal.agents.synthesis.agent import SynthesisAgent
from zion_terminal.cache.cache_manager import CacheManager
from zion_terminal.models.responses import OrchestratorResponse, RetrievalResult, ValidationResult
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
    ) -> None:
        self._cache = CacheManager(cache_dir=cache_dir, default_ttl=cache_ttl)
        self._llm = llm or NoLLMProvider()

        self._retrieval = RetrievalAgent(
            cache=self._cache,
            fred_api_key=fred_api_key,
            edgar_identity=edgar_identity,
        )
        self._validation = ValidationAgent()
        self._synthesis = SynthesisAgent(llm=self._llm)
        self._parser = IntentParser(llm=self._llm)

        logger.info("Orchestrator ready — sources: %s, LLM: %s",
                     self._retrieval.available_sources, self._llm.name)

    @property
    def available_sources(self) -> list[str]:
        return self._retrieval.available_sources

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

        retrieval_result: RetrievalResult = self._retrieval.fetch(parsed.tasks)
        results: list = [retrieval_result]

        if validate and retrieval_result.success and retrieval_result.data:
            validation_result: ValidationResult = self._validation.validate_retrieval(retrieval_result)
            results.append(validation_result)

        return OrchestratorResponse(
            success=retrieval_result.success, query=text, intent=parsed.intent,
            results=results, errors=retrieval_result.errors,
            metadata={"tickers": parsed.tickers, "macro_series": parsed.macro_series,
                       "tasks_count": len(parsed.tasks), "llm_provider": self._llm.name},
        )

    def _handle_synthesis(self, parsed: ParsedIntent) -> OrchestratorResponse:
        result = self._synthesis.generate(parsed.raw_query, parsed.params)

        # Validate synthetic output
        if result.success and result.documents:
            validation = self._validation.validate_synthesis(result)
            return OrchestratorResponse(
                success=result.success, query=parsed.raw_query, intent="synthesis",
                results=[result, validation], errors=result.errors,
            )

        return OrchestratorResponse(
            success=result.success, query=parsed.raw_query, intent="synthesis",
            results=[result], errors=result.errors,
        )

    def close(self) -> None:
        self._cache.close()
