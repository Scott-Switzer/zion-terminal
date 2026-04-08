"""Financial data models shared across all agents."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StatementType(str, Enum):
    INCOME = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"


class FilingType(str, Enum):
    TEN_K = "10-K"
    TEN_Q = "10-Q"
    EIGHT_K = "8-K"
    PROXY = "DEF 14A"
    OTHER = "other"


class FinancialDataPoint(BaseModel):
    name: str
    value: float | None = None
    unit: str = "USD"
    period: str | None = None
    as_of_date: date | None = None
    source: str | None = None


class StockQuote(BaseModel):
    ticker: str
    company_name: str | None = None
    price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    dividend_yield: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    as_of: datetime | None = None
    source: str = "yahoo_finance"


class TimeSeriesData(BaseModel):
    name: str
    ticker: str | None = None
    frequency: str = "daily"
    data_points: list[dict[str, Any]] = Field(default_factory=list)
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MacroIndicator(BaseModel):
    series_id: str
    title: str | None = None
    value: float | None = None
    unit: str | None = None
    frequency: str | None = None
    observation_date: date | None = None
    source: str = "fred"
    notes: str | None = None
    data_points: list[dict[str, Any]] = Field(default_factory=list)


class FinancialStatement(BaseModel):
    ticker: str
    company_name: str | None = None
    statement_type: StatementType
    period: str
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    currency: str = "USD"
    line_items: dict[str, float | None] = Field(default_factory=dict)
    source: str | None = None
    filing_date: date | None = None

    def get(self, key: str, default: float | None = None) -> float | None:
        return self.line_items.get(key, default)


class SECFiling(BaseModel):
    ticker: str | None = None
    company_name: str | None = None
    cik: str | None = None
    filing_type: FilingType
    filing_date: date | None = None
    accession_number: str | None = None
    document_url: str | None = None
    description: str | None = None
    content_text: str | None = None
    content_markdown: str | None = None
    source: str = "sec_edgar"
