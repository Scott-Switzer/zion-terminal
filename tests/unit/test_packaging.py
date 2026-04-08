"""Tests for package structure and imports."""

import importlib
import pytest


class TestPackageImports:
    """Verify that all public modules import cleanly."""

    def test_import_root(self):
        mod = importlib.import_module("zion_terminal")
        assert hasattr(mod, "__version__")
        assert mod.__version__ == "0.4.0"

    def test_import_config(self):
        mod = importlib.import_module("zion_terminal.config")
        assert hasattr(mod, "get_settings")

    def test_import_settings(self):
        from zion_terminal.config.settings import Settings
        assert Settings is not None

    def test_import_providers(self):
        from zion_terminal.providers.base import (
            BaseLLMProvider, NoLLMProvider, OpenAIProvider, OllamaProvider, build_provider,
        )
        assert BaseLLMProvider is not None

    def test_import_models(self):
        from zion_terminal.models.financial import StockQuote, SECFiling
        from zion_terminal.models.responses import (
            RetrievalResult, SynthesisResult, ValidationResult, OrchestratorResponse,
        )
        assert StockQuote is not None
        assert RetrievalResult is not None

    def test_import_retrieval_agent(self):
        from zion_terminal.agents.retrieval.agent import RetrievalAgent
        assert RetrievalAgent is not None

    def test_import_adapters(self):
        from zion_terminal.agents.retrieval.adapters.yahoo_finance import YahooFinanceAdapter
        from zion_terminal.agents.retrieval.adapters.fred import FREDAdapter
        from zion_terminal.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter
        assert YahooFinanceAdapter is not None

    def test_import_synthesis_agent(self):
        from zion_terminal.agents.synthesis.agent import SynthesisAgent
        assert SynthesisAgent is not None

    def test_import_validation_agent(self):
        from zion_terminal.agents.validation.agent import ValidationAgent
        assert ValidationAgent is not None

    def test_import_orchestrator(self):
        from zion_terminal.orchestrator.orchestrator import Orchestrator
        assert Orchestrator is not None

    def test_import_intent_parser(self):
        from zion_terminal.orchestrator.intent_parser import IntentParser, ParsedIntent
        assert IntentParser is not None

    def test_import_formatter(self):
        from zion_terminal.outputs.formatter import format_response, OutputFormat
        assert format_response is not None

    def test_import_cli(self):
        from zion_terminal.cli import main
        assert main is not None

    def test_import_cache(self):
        from zion_terminal.cache.cache_manager import CacheManager
        assert CacheManager is not None

    def test_import_retry(self):
        from zion_terminal.agents.retrieval.retry import adapter_retry
        assert adapter_retry is not None


class TestEntryPoint:
    def test_cli_entry_point_importable(self):
        """Verify the entry point target is importable."""
        from zion_terminal.cli import main
        assert callable(main)
