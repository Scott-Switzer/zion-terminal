"""Tests for verification/reconciliation modules."""
import pytest
from zion_terminal.verification.fact_mapping import CanonicalFact, map_xbrl_fact
from zion_terminal.verification.markdown_extractor import (
    extract_tables, parse_numeric, extract_values,
)
from zion_terminal.verification.reconciler import Reconciler, ReconciliationReport


class TestMarkdownExtractor:
    def test_extract_simple_table(self):
        md = """| Metric | Value |
| --- | --- |
| Revenue | $50M |
| Net Income | $8M |"""
        tables = extract_tables(md)
        assert len(tables) == 1
        assert len(tables[0]) == 3  # header + 2 data rows

    def test_parse_numeric_dollar(self):
        assert parse_numeric("$1,234.56") == 1234.56

    def test_parse_numeric_millions(self):
        assert parse_numeric("$50M") == 50_000_000

    def test_parse_numeric_billions(self):
        assert parse_numeric("$1.5B") == 1_500_000_000

    def test_parse_numeric_parenthetical_negative(self):
        assert parse_numeric("(1,234)") == -1234

    def test_parse_numeric_percentage(self):
        assert abs(parse_numeric("22%") - 0.22) < 0.001

    def test_parse_numeric_none_for_text(self):
        assert parse_numeric("N/A") is None
        assert parse_numeric("") is None

    def test_extract_values_from_table(self):
        md = """## Results
| Metric | 2025 | 2024 |
| --- | --- | --- |
| Revenue | $50M | $43.5M |
| Net Income | $8M | $6.2M |"""
        values = extract_values(md)
        assert len(values) >= 2
        labels = {v.label for v in values}
        assert "Revenue" in labels
        assert "Net Income" in labels


class TestReconciler:
    def test_exact_match(self):
        facts = [CanonicalFact(concept="us-gaap:Revenues", local_name="Revenues", value=50000000)]
        md_vals = [type("EV", (), {"label": "Revenue", "value": 50000000, "raw_text": "$50M", "table_index": 0, "row_index": 1})()]
        r = Reconciler()
        report = r.reconcile(facts, md_vals)
        assert report.facts_matched == 1
        assert report.match_rate == 1.0

    def test_scale_mismatch_detected(self):
        facts = [CanonicalFact(concept="us-gaap:Revenues", local_name="Revenues", value=50000000)]
        md_vals = [type("EV", (), {"label": "Revenue", "value": 50.0, "raw_text": "$50M", "table_index": 0, "row_index": 1})()]
        r = Reconciler()
        report = r.reconcile(facts, md_vals)
        assert report.facts_scale_mismatch >= 1 or report.facts_matched == 0

    def test_missing_fact(self):
        facts = [CanonicalFact(concept="us-gaap:Assets", local_name="Assets", value=100000)]
        md_vals = []  # empty markdown
        r = Reconciler()
        report = r.reconcile(facts, md_vals)
        assert report.facts_missing == 1

    def test_report_to_dict(self):
        r = Reconciler()
        report = r.reconcile([], [])
        d = report.to_dict()
        assert "match_rate" in d
        assert d["facts_compared"] == 0


class TestFactMapping:
    def test_canonical_fact_to_dict(self):
        f = CanonicalFact(concept="us-gaap:Revenue", local_name="Revenue", value=1000)
        d = f.to_dict()
        assert d["concept"] == "us-gaap:Revenue"
        assert d["value"] == 1000

    def test_map_xbrl_fact_with_mock(self):
        from unittest.mock import MagicMock
        fact = MagicMock()
        fact.concept.qname.localName = "Revenue"
        fact.concept.qname.prefix = "us-gaap"
        fact.concept.qname.namespaceURI = "http://fasb.org/us-gaap"
        fact.xValue = 1000000
        fact.value = "1000000"
        fact.unit.measures = [["iso4217:USD"]]
        fact.context.isStartEndPeriod = True
        fact.context.isInstantPeriod = False
        fact.context.startDatetime.date.return_value = "2024-01-01"
        fact.context.endDatetime.date.return_value = "2024-12-31"
        fact.decimals = "-3"
        
        result = map_xbrl_fact(fact)
        assert result.local_name == "Revenue"
        assert result.value == 1000000
        assert result.period_type == "duration"
