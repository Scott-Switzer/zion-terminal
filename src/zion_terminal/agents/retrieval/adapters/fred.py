"""FRED (Federal Reserve Economic Data) adapter.

Status: working. Requires a free FRED API key.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from zion_terminal.agents.retrieval.base_adapter import BaseAdapter
from zion_terminal.agents.retrieval.retry import adapter_retry
from zion_terminal.models.financial import MacroIndicator
from zion_terminal.models.responses import RetrievalResult

logger = logging.getLogger(__name__)

SERIES_ALIASES: dict[str, str] = {
    "fed_funds": "FEDFUNDS", "fed_funds_rate": "FEDFUNDS", "federal_funds_rate": "FEDFUNDS",
    "gdp": "GDP", "real_gdp": "GDPC1",
    "cpi": "CPIAUCSL", "inflation": "CPIAUCSL",
    "unemployment": "UNRATE", "unemployment_rate": "UNRATE",
    "10y_treasury": "DGS10", "10_year_treasury": "DGS10", "2y_treasury": "DGS2",
    "2_year_treasury": "DGS2", "30y_mortgage": "MORTGAGE30US",
    "sp500": "SP500", "m2": "M2SL", "pce": "PCEPI", "core_pce": "PCEPILFE",
    "housing_starts": "HOUST", "retail_sales": "RSXFS",
    "industrial_production": "INDPRO", "consumer_sentiment": "UMCSENT", "vix": "VIXCLS",
}


def _resolve_series_id(raw: str) -> str:
    normalised = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return SERIES_ALIASES.get(normalised, raw.strip().upper())


class FREDAdapter(BaseAdapter):
    SOURCE_NAME = "fred"
    SUPPORTED_CATEGORIES = ["macro", "economic", "interest_rate", "indicator"]

    def __init__(self, api_key: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key

    def _get_fred(self):
        from fredapi import Fred
        if not self._api_key:
            raise ValueError("FRED_API_KEY is required. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")
        return Fred(api_key=self._api_key)

    def fetch(self, params: dict[str, Any]) -> RetrievalResult:
        raw_id = params.get("series_id", "")
        if not raw_id:
            return RetrievalResult(success=False, errors=["Missing required parameter: series_id"])

        series_id = _resolve_series_id(raw_id)
        start_date = params.get("start_date")
        end_date = params.get("end_date")
        cache_params: dict[str, Any] = {"series_id": series_id}
        if start_date:
            cache_params["start_date"] = str(start_date)
        if end_date:
            cache_params["end_date"] = str(end_date)
        cached = self._cache_get(cache_params)
        if cached is not None:
            return RetrievalResult(data=cached, sources_used=[self.SOURCE_NAME], cached=True)

        try:
            result_data = self._do_fetch(series_id, params)
            self._cache_set(cache_params, result_data)
            return RetrievalResult(data=result_data, sources_used=[self.SOURCE_NAME])
        except ValueError as exc:
            return RetrievalResult(success=False, errors=[str(exc)])
        except Exception as exc:
            logger.exception("FRED fetch failed for %s", raw_id)
            return RetrievalResult(success=False, errors=[f"FRED API error: {exc}"])

    @adapter_retry
    def _do_fetch(self, series_id: str, params: dict) -> list[dict]:
        fred = self._get_fred()
        info = fred.get_series_info(series_id)
        series: pd.Series = fred.get_series(
            series_id,
            observation_start=params.get("start_date"),
            observation_end=params.get("end_date"),
        )

        data_points = []
        for dt, val in series.items():
            if pd.notna(val):
                data_points.append({
                    "date": str(dt.date()) if hasattr(dt, "date") else str(dt),
                    "value": round(float(val), 6),
                })

        latest_val = data_points[-1]["value"] if data_points else None
        latest_date = date.fromisoformat(data_points[-1]["date"]) if data_points else None

        def _info_get(key: str, default: str = "") -> str:
            return str(info.get(key, default)) if hasattr(info, "get") else str(getattr(info, key, default))

        indicator = MacroIndicator(
            series_id=series_id,
            title=_info_get("title", series_id),
            value=latest_val,
            unit=_info_get("units"),
            frequency=_info_get("frequency"),
            observation_date=latest_date,
            source=self.SOURCE_NAME,
            notes=_info_get("notes")[:500],
            data_points=data_points[-60:],
        )
        return [indicator.model_dump()]

    @staticmethod
    def list_common_series() -> dict[str, str]:
        return dict(SERIES_ALIASES)
