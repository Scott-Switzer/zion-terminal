"""Yahoo Finance adapter using yfinance.

Status: working, free, no API key required.
Caveat: unofficial API, may break. Suitable for development and personal use.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd
import yfinance as yf
from zion_terminal.agents.retrieval.base_adapter import BaseAdapter
from zion_terminal.agents.retrieval.retry import adapter_retry
from zion_terminal.models.financial import FinancialStatement, StatementType, StockQuote, TimeSeriesData
from zion_terminal.models.responses import RetrievalResult

logger = logging.getLogger(__name__)


class YahooFinanceAdapter(BaseAdapter):
    SOURCE_NAME = "yahoo_finance"
    SUPPORTED_CATEGORIES = ["equities", "stock", "quote", "financials", "historical"]

    def fetch(self, params: dict[str, Any]) -> RetrievalResult:
        action = params.get("action", "quote")
        ticker = params.get("ticker", "").upper()

        if not ticker:
            return RetrievalResult(success=False, errors=["Missing required parameter: ticker"])

        cached = self._cache_get(params)
        if cached is not None:
            return RetrievalResult(data=cached, sources_used=[self.SOURCE_NAME], cached=True)

        try:
            dispatch = {
                "quote": self._fetch_quote,
                "history": self._fetch_history,
                "financials": self._fetch_financials,
                "info": self._fetch_info,
            }
            handler = dispatch.get(action)
            if handler is None:
                return RetrievalResult(success=False, errors=[f"Unknown action: {action}"])

            result = handler(ticker, params)
            if result.success:
                self._cache_set(params, result.data)
            return result
        except Exception as exc:
            logger.exception("Yahoo Finance fetch failed for %s", ticker)
            return RetrievalResult(success=False, errors=[f"Yahoo Finance error: {exc}"])

    @adapter_retry
    def _fetch_quote(self, ticker: str, params: dict) -> RetrievalResult:
        stock = yf.Ticker(ticker)
        info = stock.info
        quote = StockQuote(
            ticker=ticker,
            company_name=info.get("shortName") or info.get("longName"),
            price=info.get("currentPrice") or info.get("regularMarketPrice"),
            open=info.get("regularMarketOpen"),
            high=info.get("regularMarketDayHigh"),
            low=info.get("regularMarketDayLow"),
            close=info.get("previousClose"),
            volume=info.get("regularMarketVolume"),
            market_cap=info.get("marketCap"),
            pe_ratio=info.get("trailingPE"),
            dividend_yield=info.get("dividendYield"),
            fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
            fifty_two_week_low=info.get("fiftyTwoWeekLow"),
            as_of=datetime.now(),
            source=self.SOURCE_NAME,
        )
        return RetrievalResult(data=[quote.model_dump()], sources_used=[self.SOURCE_NAME])

    @adapter_retry
    def _fetch_history(self, ticker: str, params: dict) -> RetrievalResult:
        period = params.get("period", "1y")
        interval = params.get("interval", "1d")
        stock = yf.Ticker(ticker)
        hist: pd.DataFrame = stock.history(period=period, interval=interval)
        if hist.empty:
            return RetrievalResult(success=False, errors=[f"No historical data for {ticker}"])

        records = []
        for dt, row in hist.iterrows():
            records.append({
                "date": str(dt.date()) if hasattr(dt, "date") else str(dt),
                "open": round(float(row.get("Open", 0)), 4),
                "high": round(float(row.get("High", 0)), 4),
                "low": round(float(row.get("Low", 0)), 4),
                "close": round(float(row.get("Close", 0)), 4),
                "volume": int(row.get("Volume", 0)),
            })

        ts = TimeSeriesData(
            name=f"{ticker} Historical Prices",
            ticker=ticker,
            frequency=interval,
            data_points=records,
            source=self.SOURCE_NAME,
            metadata={"period": period, "interval": interval},
        )
        return RetrievalResult(data=[ts.model_dump()], sources_used=[self.SOURCE_NAME])

    @adapter_retry
    def _fetch_financials(self, ticker: str, params: dict) -> RetrievalResult:
        stmt_type = params.get("statement_type", "income")
        quarterly = params.get("quarterly", False)
        stock = yf.Ticker(ticker)

        type_map = {
            "income": (StatementType.INCOME, stock.quarterly_income_stmt if quarterly else stock.income_stmt),
            "balance": (StatementType.BALANCE_SHEET, stock.quarterly_balance_sheet if quarterly else stock.balance_sheet),
            "cash_flow": (StatementType.CASH_FLOW, stock.quarterly_cashflow if quarterly else stock.cashflow),
        }

        if stmt_type not in type_map:
            return RetrievalResult(success=False, errors=[f"Unknown statement type: {stmt_type}. Use income/balance/cash_flow."])

        stype, df = type_map[stmt_type]
        if df is None or df.empty:
            return RetrievalResult(success=False, errors=[f"No {stmt_type} data for {ticker}"])

        results = []
        for col in df.columns:
            period_label = str(col.date()) if hasattr(col, "date") else str(col)
            line_items: dict[str, float | None] = {}
            for idx in df.index:
                val = df.loc[idx, col]
                line_items[str(idx)] = float(val) if pd.notna(val) else None

            stmt = FinancialStatement(
                ticker=ticker,
                company_name=stock.info.get("shortName"),
                statement_type=stype,
                period=period_label,
                currency="USD",
                line_items=line_items,
                source=self.SOURCE_NAME,
            )
            results.append(stmt.model_dump())
        return RetrievalResult(data=results, sources_used=[self.SOURCE_NAME])

    @adapter_retry
    def _fetch_info(self, ticker: str, params: dict) -> RetrievalResult:
        stock = yf.Ticker(ticker)
        info = stock.info
        return RetrievalResult(
            data=[{
                "ticker": ticker,
                "type": "company_info",
                "data": {k: v for k, v in info.items() if isinstance(v, (str, int, float, bool, type(None)))},
                "source": self.SOURCE_NAME,
            }],
            sources_used=[self.SOURCE_NAME],
        )
