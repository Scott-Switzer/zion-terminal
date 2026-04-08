"""Intent parser – converts natural-language queries into structured tasks.

Uses a combination of keyword matching and optional LLM calls to figure
out what the user wants and which agents need to be involved.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Ticker extraction ────────────────────────────────────────────────────

_TICKER_PATTERN = re.compile(
    r"\b([A-Z]{1,5})\b(?:'s)?",
)

# Words that look like tickers but are not
_TICKER_STOP_WORDS = {
    "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OK", "ON", "OR", "SO",
    "TO", "UP", "US", "WE", "THE", "AND", "FOR", "ARE", "BUT", "NOT",
    "YOU", "ALL", "HER", "WAS", "ONE", "OUR", "OUT", "HAS", "HIS",
    "HOW", "ITS", "MAY", "NEW", "NOW", "OLD", "SEE", "WAY", "WHO",
    "DID", "GET", "HIM", "LET", "SAY", "SHE", "TOO", "USE", "SEC",
    "GDP", "CPI", "FED", "PULL", "SHOW", "GIVE", "WHAT", "YEAR",
    "FROM", "LAST", "THIS", "THAT", "WITH", "FIND", "RATE", "DATA",
    "ALSO", "CASH", "FLOW", "MOST", "LIST", "WILL", "EACH", "MAKE",
    "LIKE", "LONG", "LOOK", "MANY", "NEXT", "ONLY", "OVER", "SUCH",
    "TAKE", "THAN", "THEM", "THEN", "WELL", "WERE", "CURRENT",
    "QUARTERLY", "ANNUAL", "FISCAL", "NET", "INCOME", "REVENUE",
}


def extract_tickers(text: str) -> list[str]:
    """Pull likely stock tickers out of a query string."""
    candidates = _TICKER_PATTERN.findall(text)
    return [c for c in candidates if c not in _TICKER_STOP_WORDS and len(c) >= 1]


# ── FRED series detection ───────────────────────────────────────────────

_MACRO_KEYWORDS: dict[str, str] = {
    "fed funds": "FEDFUNDS",
    "federal funds": "FEDFUNDS",
    "fed funds rate": "FEDFUNDS",
    "federal funds rate": "FEDFUNDS",
    "interest rate": "FEDFUNDS",
    "gdp": "GDP",
    "gross domestic product": "GDP",
    "real gdp": "GDPC1",
    "cpi": "CPIAUCSL",
    "consumer price index": "CPIAUCSL",
    "inflation": "CPIAUCSL",
    "unemployment": "UNRATE",
    "unemployment rate": "UNRATE",
    "10 year treasury": "DGS10",
    "10-year treasury": "DGS10",
    "10y treasury": "DGS10",
    "2 year treasury": "DGS2",
    "2-year treasury": "DGS2",
    "30 year mortgage": "MORTGAGE30US",
    "mortgage rate": "MORTGAGE30US",
    "consumer sentiment": "UMCSENT",
    "housing starts": "HOUST",
    "retail sales": "RSXFS",
    "industrial production": "INDPRO",
    "money supply": "M2SL",
    "m2": "M2SL",
    "pce": "PCEPI",
    "core pce": "PCEPILFE",
    "vix": "VIXCLS",
    "sp500": "SP500",
    "s&p 500": "SP500",
    "s&p500": "SP500",
}


def detect_macro_series(text: str) -> list[str]:
    """Find FRED series IDs mentioned in the query."""
    lower = text.lower()
    found: list[str] = []
    for phrase, sid in _MACRO_KEYWORDS.items():
        if phrase in lower and sid not in found:
            found.append(sid)
    return found


# ── Intent classification ────────────────────────────────────────────────

_INTENT_KEYWORDS = {
    "quote": ["price", "quote", "trading", "current price", "stock price", "market price"],
    "history": ["historical", "history", "chart", "trend", "performance", "past", "prices"],
    "financials": ["financials", "financial statements", "income statement", "balance sheet",
                   "cash flow", "revenue", "earnings", "net income", "quarterly financials",
                   "annual report"],
    "filings": ["filing", "filings", "10-k", "10-q", "8-k", "sec filing", "sec filings",
                "edgar", "annual filing", "quarterly filing", "proxy"],
    "macro": ["macro", "economic", "fed", "gdp", "cpi", "inflation", "unemployment",
              "interest rate", "treasury", "indicator", "monetary", "fiscal"],
    "company_info": ["company info", "about", "sector", "industry", "description",
                     "profile", "overview"],
    "synthesis": ["generate", "create", "synthetic", "fake", "simulate", "fictional",
                  "mock", "fabricate"],
}


@dataclass
class ParsedIntent:
    """The result of parsing a user query."""
    raw_query: str
    intent: str = "unknown"
    tickers: list[str] = field(default_factory=list)
    macro_series: list[str] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_retrieval(self) -> bool:
        return self.intent in ("quote", "history", "financials", "filings", "macro", "company_info", "multi")

    @property
    def needs_synthesis(self) -> bool:
        return self.intent == "synthesis"


class IntentParser:
    """Parse natural-language queries into structured fetch tasks.

    This is the rule-based parser.  It can optionally call an LLM for
    ambiguous queries if an OpenAI client is provided.
    """

    def __init__(self, openai_client=None, model: str = "gpt-4o-mini") -> None:
        self._client = openai_client
        self._model = model

    def parse(self, query: str) -> ParsedIntent:
        """Parse a query into a structured intent."""
        tickers = extract_tickers(query)
        macro_series = detect_macro_series(query)
        intent = self._classify_intent(query)

        # Build tasks
        tasks: list[dict[str, Any]] = []

        # If we found both tickers and macro series, it's a multi-source query
        if tickers and macro_series:
            intent = "multi"

        # Handle synthesis intent directly
        if intent == "synthesis":
            tasks.append({"_intent": "synthesis", "source": "synthesis", "query": query})
            parsed = ParsedIntent(
                raw_query=query,
                intent=intent,
                tickers=tickers,
                macro_series=macro_series,
                tasks=tasks,
            )
            return parsed

        if tickers:
            for ticker in tickers:
                task = self._build_equity_task(ticker, intent, query)
                tasks.append(task)

        if macro_series:
            for sid in macro_series:
                tasks.append({"source": "fred", "series_id": sid})

        # If we have no tasks but have a query, try LLM parsing
        if not tasks and self._client:
            tasks = self._llm_parse(query)
            if tasks:
                intent = "multi" if len(tasks) > 1 else tasks[0].get("_intent", "unknown")

        parsed = ParsedIntent(
            raw_query=query,
            intent=intent,
            tickers=tickers,
            macro_series=macro_series,
            tasks=tasks,
        )

        # Extract extra params from query
        if "quarterly" in query.lower():
            parsed.params["quarterly"] = True
        if "annual" in query.lower():
            parsed.params["quarterly"] = False

        logger.info("Parsed query: intent=%s, tickers=%s, macro=%s, tasks=%d",
                     intent, tickers, macro_series, len(tasks))
        return parsed

    def _classify_intent(self, query: str) -> str:
        """Rule-based intent classification."""
        lower = query.lower()
        scores: dict[str, int] = {}
        for intent, keywords in _INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in lower)
            if score > 0:
                scores[intent] = score

        if not scores:
            return "quote"  # default intent
        return max(scores, key=scores.get)  # type: ignore[arg-type]

    def _build_equity_task(self, ticker: str, intent: str, query: str) -> dict[str, Any]:
        """Create a fetch task for an equity ticker."""
        if intent in ("filings",):
            form = None
            lower = query.lower()
            if "10-k" in lower:
                form = "10-K"
            elif "10-q" in lower:
                form = "10-Q"
            elif "8-k" in lower:
                form = "8-K"
            return {
                "source": "sec_edgar",
                "ticker": ticker,
                "action": "filings",
                "form": form,
                "limit": 10,
            }
        elif intent == "history":
            return {
                "source": "yahoo_finance",
                "ticker": ticker,
                "action": "history",
                "period": "1y",
                "interval": "1d",
            }
        elif intent == "financials":
            stmt_type = "income"
            lower = query.lower()
            if "balance" in lower:
                stmt_type = "balance"
            elif "cash flow" in lower:
                stmt_type = "cash_flow"
            quarterly = "quarterly" in lower or "quarter" in lower
            return {
                "source": "yahoo_finance",
                "ticker": ticker,
                "action": "financials",
                "statement_type": stmt_type,
                "quarterly": quarterly,
            }
        elif intent == "company_info":
            return {
                "source": "yahoo_finance",
                "ticker": ticker,
                "action": "info",
            }
        else:
            # Default to quote
            return {
                "source": "yahoo_finance",
                "ticker": ticker,
                "action": "quote",
            }

    def _llm_parse(self, query: str) -> list[dict[str, Any]]:
        """Use an LLM to parse ambiguous queries."""
        if not self._client:
            return []

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You parse financial data queries into structured tasks. "
                            "Return a JSON array of tasks. Each task has: "
                            "source (yahoo_finance|fred|sec_edgar), "
                            "and source-specific params (ticker, action, series_id, etc). "
                            "Valid yahoo_finance actions: quote, history, financials, info. "
                            "Valid sec_edgar actions: filings, financials, company_facts. "
                            "FRED tasks need series_id. "
                            "Return ONLY the JSON array, no markdown."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_tokens=500,
            )
            raw = response.choices[0].message.content or "[]"
            # Strip markdown code blocks if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```\w*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            return json.loads(raw)
        except Exception as exc:
            logger.warning("LLM parse failed: %s", exc)
            return []
