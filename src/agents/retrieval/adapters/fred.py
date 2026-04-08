"""FRED (Federal Reserve Economic Data) adapter.

Covers: macroeconomic indicators – GDP, CPI, unemployment, Fed Funds rate,
Treasury yields, and thousands of other series.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import pandas as pd

from src.agents.retrieval.base_adapter import BaseAdapter
from src.models.financial import MacroIndicator
from src.models.responses import RetrievalResult

logger = logging.getLogger(__name__)

# Common FRED series shortcuts
SERIES_ALIASES: dict[str, str] = {
    "fed_funds": "FEDFUNDS",
    "fed_funds_rate": "FEDFUNDS",
    "federal_funds_rate": "FEDFUNDS",
    "gdp": "GDP",
    "real_gdp": "GDPC1",
    "cpi": "CPIAUCSL",
    "inflation": "CPIAUCSL",
    "unemployment": "UNRATE",
    "unemployment_rate": "UNRATE",
    "10y_treasury": "DGS10",
    "10_year_treasury": "DGS10",
    "2y_treasury": "DGS2",
    "2_year_treasury": "DGS2",
    "30y_mortgage": "MORTGAGE30US",
    "sp500": "SP500",
    "m2": "M2SL",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    "housing_starts": "HOUST",
    "retail_sales": "RSXFS",
    "industrial_production": "INDPRO",
    "consumer_sentiment": "UMCSENT",
    "vix": "VIXCLS",
}


def _resolve_series_id(raw: str) -> str:
    """Map human-friendly names to official FRED series IDs."""
    normalised = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return SERIES_ALIASES.get(normalised, raw.strip().upper())


class FREDAdapter(BaseAdapter):
    """Fetch macroeconomic data from the FRED API."""

    SOURCE_NAME = "fred"
    SUPPORTED_CATEGORIES = ["macro", "economic", "interest_rate", "indicator"]

    def __init__(self, api_key: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key

    def _get_fred(self):
        """Lazy import fredapi to keep it optional at import time."""
        from fredapi import Fred
        if not self._api_key:
            raise ValueError(
                "FRED_API_KEY is required. Get a free key at "
                "https://fred.stlouisfed.org/docs/api/api_key.html"
            )
        return Fred(api_key=self._api_key)

    def fetch(self, params: dict[str, Any]) -> RetrievalResult:
        """Fetch a FRED series by ID or alias."""
        raw_id = params.get("series_id", "")
        if not raw_id:
            return RetrievalResult(
                success=False,
                errors=["Missing required parameter: series_id"],
            )

        series_id = _resolve_series_id(raw_id)

        # Check cache first
        cache_params = {"series_id": series_id, **{k: v for k, v in params.items() if k != "series_id"}}
        cached = self._cache_get(cache_params)
        if cached is not None:
            return RetrievalResult(data=cached, sources_used=[self.SOURCE_NAME], cached=True)

        try:
            fred = self._get_fred()
            info = fred.get_series_info(series_id)

            # Fetch the actual data series
            start = params.get("start_date")
            end = params.get("end_date")
            series: pd.Series = fred.get_series(
                series_id,
                observation_start=start,
                observation_end=end,
            )

            # Build data points list
            data_points = []
            for dt, val in series.items():
                if pd.notna(val):
                    data_points.append({
                        "date": str(dt.date()) if hasattr(dt, "date") else str(dt),
                        "value": round(float(val), 6),
                    })

            # Latest value
            latest_val = None
            latest_date = None
            if data_points:
                latest_val = data_points[-1]["value"]
                latest_date = date.fromisoformat(data_points[-1]["date"])

            indicator = MacroIndicator(
                series_id=series_id,
                title=str(info.get("title", series_id)) if hasattr(info, "get") else str(getattr(info, "title", series_id)),
                value=latest_val,
                unit=str(info.get("units", "")) if hasattr(info, "get") else str(getattr(info, "units", "")),
                frequency=str(info.get("frequency", "")) if hasattr(info, "get") else str(getattr(info, "frequency", "")),
                observation_date=latest_date,
                source=self.SOURCE_NAME,
                notes=str(info.get("notes", ""))[:500] if hasattr(info, "get") else str(getattr(info, "notes", ""))[:500],
                data_points=data_points[-60:],  # last 60 observations
            )

            result_data = [indicator.model_dump()]
            self._cache_set(cache_params, result_data)

            return RetrievalResult(
                data=result_data,
                sources_used=[self.SOURCE_NAME],
            )
        except ValueError as exc:
            return RetrievalResult(success=False, errors=[str(exc)])
        except Exception as exc:
            logger.exception("FRED fetch failed for %s", raw_id)
            return RetrievalResult(
                success=False,
                errors=[f"FRED API error: {exc}"],
            )

    @staticmethod
    def list_common_series() -> dict[str, str]:
        """Return a mapping of friendly names → FRED series IDs."""
        return dict(SERIES_ALIASES)
