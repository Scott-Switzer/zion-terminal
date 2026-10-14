"""Reapply proven semantic cases to the generic engine, preserving the frozen v1 tests."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"))
sys.path.insert(0, str(Path(__file__).parent))
import sec_engine
import test_sec_history as legacy

CASES = [
    name
    for name in vars(legacy)
    if name.startswith("test_")
    and name != "test_committed_registry_pins_exact_normalizer_implementation"
]


@pytest.mark.parametrize("name", CASES)
def test_generic_engine_semantic_regressions(name, monkeypatch):
    for symbol in ("normalize_sources", "history", "REGISTRY", "VERSION", "HistoryError"):
        if hasattr(legacy, symbol):
            monkeypatch.setattr(legacy, symbol, getattr(sec_engine, symbol))
    getattr(legacy, name)()
