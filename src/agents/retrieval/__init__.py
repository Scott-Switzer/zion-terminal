"""Retrieval agent – fetches financial data from public sources."""

from src.agents.retrieval.agent import RetrievalAgent
from src.agents.retrieval.adapters.yahoo_finance import YahooFinanceAdapter
from src.agents.retrieval.adapters.fred import FREDAdapter
from src.agents.retrieval.adapters.sec_edgar import SECEdgarAdapter

__all__ = [
    "RetrievalAgent",
    "YahooFinanceAdapter",
    "FREDAdapter",
    "SECEdgarAdapter",
]
