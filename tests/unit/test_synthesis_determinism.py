"""Tests for deterministic synthesis."""
import pytest
from zion_terminal.agents.synthesis.agent import SynthesisAgent


class TestDeterministicSynthesis:
    def test_same_seed_same_output(self):
        agent = SynthesisAgent()
        r1 = agent.generate("generate company", {"seed": 42})
        r2 = agent.generate("generate company", {"seed": 42})
        assert r1.documents == r2.documents
        assert r1.entity_name == r2.entity_name
        assert r1.entity_ticker == r2.entity_ticker

    def test_different_seeds_differ(self):
        agent = SynthesisAgent()
        r1 = agent.generate("generate company", {"seed": 42})
        r2 = agent.generate("generate company", {"seed": 99})
        # Very unlikely to be identical with different seeds
        assert r1.entity_name != r2.entity_name or r1.entity_ticker != r2.entity_ticker

    def test_same_query_reproducible_without_explicit_seed(self):
        agent = SynthesisAgent()
        r1 = agent.generate("generate synthetic company")
        r2 = agent.generate("generate synthetic company")
        assert r1.documents == r2.documents

    def test_different_queries_diverge(self):
        agent = SynthesisAgent()
        r1 = agent.generate("query A")
        r2 = agent.generate("query B")
        assert r1.entity_name != r2.entity_name or r1.documents != r2.documents
