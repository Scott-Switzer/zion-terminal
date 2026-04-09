"""Intent parser – converts natural-language queries into structured tasks.

This is the rule-based parser. It is first-class, not an LLM fallback crutch.
LLM-based parsing is optional and only used for ambiguous queries.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from zion_terminal.providers.base import BaseLLMProvider, NoLLMProvider

logger = logging.getLogger(__name__)

# ── Ticker extraction ────────────────────────────────────────────────────

_TICKER_PATTERN = re.compile(r"\b([A-Z]{1,5})\b")

_TICKER_STOP_WORDS = {
    "AM", "AN", "AS", "AT", "BE", "BY", "DO", "IF", "IN", "IS", "ME",
    "MY", "NO", "OF", "OK", "OR", "SO", "TO", "UP", "US", "WE",
    "THE", "AND", "FOR", "BUT", "NOT", "YOU", "HER", "WAS", "OUR",
    "OUT", "HAS", "HIS", "HOW", "ITS", "MAY", "OLD", "WHO", "DID",
    "GET", "HIM", "LET", "SAY", "SHE", "TOO", "USE", "SEC",
    "PULL", "SHOW", "GIVE", "WHAT", "YEAR", "FROM", "LAST", "THIS",
    "THAT", "WITH", "FIND", "RATE", "DATA", "ALSO", "CASH", "FLOW",
    "MOST", "LIST", "WILL", "EACH", "MAKE", "LIKE", "LONG", "LOOK",
    "MANY", "NEXT", "ONLY", "OVER", "SUCH", "TAKE", "THAN", "THEM",
    "THEN", "WELL", "WERE", "CURRENT", "QUARTERLY", "ANNUAL", "FISCAL",
    "NET", "INCOME", "REVENUE", "PRICE", "STOCK", "MARKET",
    # Form types and financial terms that look like tickers
    "K", "Q", "S", "P", "GDP", "CPI", "VIX", "PCE", "XBRL",
    "FORM", "READ", "FULL", "FRED",
    "ABOUT", "FACTS", "LEVEL", "TEXT", "ITEM", "RISK",
}

# Common company name → ticker mappings
_COMPANY_TICKERS: dict[str, str] = {
    "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL",
    "amazon": "AMZN", "meta": "META", "facebook": "META", "nvidia": "NVDA",
    "tesla": "TSLA", "netflix": "NFLX", "amd": "AMD", "intel": "INTC",
    "berkshire": "BRK-B", "jpmorgan": "JPM", "jp morgan": "JPM",
    "goldman sachs": "GS", "goldman": "GS", "morgan stanley": "MS",
    "visa": "V", "mastercard": "MA", "paypal": "PYPL",
    "disney": "DIS", "coca-cola": "KO", "coke": "KO", "pepsi": "PEP",
    "walmart": "WMT", "costco": "COST", "home depot": "HD",
    "boeing": "BA", "lockheed": "LMT", "raytheon": "RTX",
    "pfizer": "PFE", "johnson & johnson": "JNJ", "unitedhealth": "UNH",
    "exxon": "XOM", "chevron": "CVX", "conocophillips": "COP",
    "salesforce": "CRM", "adobe": "ADBE", "snowflake": "SNOW",
    "palantir": "PLTR", "coinbase": "COIN", "robinhood": "HOOD",
    "spotify": "SPOT", "uber": "UBER", "airbnb": "ABNB",
}


def _word_boundary_match(name: str, text: str) -> bool:
    """Check if `name` appears in `text` as a whole word (not as a substring).

    Uses regex word boundaries to prevent false positives like
    'meta' matching inside 'metadata'.
    """
    pattern = r"\b" + re.escape(name) + r"\b"
    return bool(re.search(pattern, text))


def extract_tickers(text: str) -> list[str]:
    """Pull likely stock tickers from a query. Supports UPPERCASE and known company names."""
    tickers: list[str] = []

    # 1. Check for known company names (case-insensitive, word-boundary matching)
    lower = text.lower()
    for name, ticker in _COMPANY_TICKERS.items():
        if _word_boundary_match(name, lower) and ticker not in tickers:
            tickers.append(ticker)

    # 2. Extract uppercase ticker patterns
    candidates = _TICKER_PATTERN.findall(text)
    for c in candidates:
        if c not in _TICKER_STOP_WORDS and c not in tickers:
            tickers.append(c)

    # 3. Check for lowercase tickers that look like tickers (e.g. "aapl")
    lower_ticker_pattern = re.compile(r"\b([a-z]{2,5})\b")
    lower_candidates = lower_ticker_pattern.findall(text)
    for c in lower_candidates:
        upper = c.upper()
        if upper in _COMPANY_TICKERS.values() and upper not in tickers:
            tickers.append(upper)

    return tickers


# ── FRED series detection ────────────────────────────────────────────────

_MACRO_KEYWORDS: dict[str, str] = {
    "fed funds": "FEDFUNDS", "federal funds": "FEDFUNDS", "fed funds rate": "FEDFUNDS",
    "federal funds rate": "FEDFUNDS", "interest rate": "FEDFUNDS",
    "gdp": "GDP", "gross domestic product": "GDP", "real gdp": "GDPC1",
    "cpi": "CPIAUCSL", "consumer price index": "CPIAUCSL", "inflation": "CPIAUCSL",
    "unemployment": "UNRATE", "unemployment rate": "UNRATE",
    "10 year treasury": "DGS10", "10-year treasury": "DGS10", "10y treasury": "DGS10",
    "2 year treasury": "DGS2", "2-year treasury": "DGS2",
    "30 year mortgage": "MORTGAGE30US", "mortgage rate": "MORTGAGE30US",
    "consumer sentiment": "UMCSENT", "housing starts": "HOUST",
    "retail sales": "RSXFS", "industrial production": "INDPRO",
    "money supply": "M2SL", "m2": "M2SL", "pce": "PCEPI", "core pce": "PCEPILFE",
    "vix": "VIXCLS", "sp500": "SP500", "s&p 500": "SP500", "s&p500": "SP500",
}


def detect_macro_series(text: str) -> list[str]:
    lower = text.lower()
    found: list[str] = []
    for phrase, sid in sorted(_MACRO_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if phrase in lower and sid not in found:
            found.append(sid)
    return found


# ── Parameter extraction ─────────────────────────────────────────────────

_PERIOD_PATTERN = re.compile(r"\b(\d+)\s*(d|day|days|w|wk|week|weeks|mo|month|months|y|yr|year|years)\b", re.IGNORECASE)
_PERIOD_MAP = {"d": "d", "day": "d", "days": "d", "w": "wk", "wk": "wk", "week": "wk", "weeks": "wk",
               "mo": "mo", "month": "mo", "months": "mo", "y": "y", "yr": "y", "year": "y", "years": "y"}

_INTERVAL_PATTERN = re.compile(r"\b(daily|weekly|monthly|1d|1wk|1mo|5d)\b", re.IGNORECASE)
_INTERVAL_MAP = {"daily": "1d", "weekly": "1wk", "monthly": "1mo", "1d": "1d", "1wk": "1wk", "1mo": "1mo", "5d": "5d"}

_LIMIT_PATTERN = re.compile(
    r"\b(?:last|top|limit|recent)\s+(\d+)(?!\s*(?:d|day|days|w|wk|week|weeks|mo|month|months|y|yr|year|years)\b)",
    re.IGNORECASE,
)


def extract_period(text: str) -> str | None:
    m = _PERIOD_PATTERN.search(text)
    if m:
        num = m.group(1)
        unit = _PERIOD_MAP.get(m.group(2).lower(), "d")
        return f"{num}{unit}"
    if "ytd" in text.lower():
        return "ytd"
    if "max" in text.lower():
        return "max"
    return None


def extract_interval(text: str) -> str | None:
    m = _INTERVAL_PATTERN.search(text)
    return _INTERVAL_MAP.get(m.group(1).lower()) if m else None


def extract_limit(text: str) -> int | None:
    m = _LIMIT_PATTERN.search(text)
    return int(m.group(1)) if m else None


# ── Intent classification ────────────────────────────────────────────────

_INTENT_KEYWORDS = {
    "quote": ["price", "prices", "quote", "trading", "current price", "stock price", "market price"],
    "history": ["historical", "history", "chart", "trend", "performance", "past", "prices", "ohlcv"],
    "financials": ["financials", "financial statements", "income statement", "balance sheet",
                   "cash flow", "revenue", "earnings", "net income", "quarterly financials", "annual report"],
    "filings": ["filing", "filings", "10-k", "10-q", "8-k", "sec filing", "sec filings",
                "edgar", "annual filing", "quarterly filing", "proxy"],
    "filing_markdown": ["filing markdown", "filing text", "filing content", "read filing",
                        "filing document", "full filing", "read the filing",
                        "convert filing", "filing to markdown"],
    "company_facts": ["company facts", "xbrl facts", "xbrl data", "xbrl"],
    "macro": ["macro", "economic", "fed", "gdp", "cpi", "inflation", "unemployment",
              "interest rate", "treasury", "indicator", "monetary", "fiscal"],
    "company_info": ["company info", "about", "sector", "industry", "description", "profile", "overview"],
    "synthesis": ["generate", "create", "synthetic", "fake", "simulate", "fictional", "mock"],
}


@dataclass
class ParsedIntent:
    raw_query: str
    intent: str = "unknown"
    tickers: list[str] = field(default_factory=list)
    macro_series: list[str] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_retrieval(self) -> bool:
        return self.intent in (
            "quote", "history", "financials", "filings",
            "filing_markdown", "company_facts",
            "macro", "company_info", "multi",
        )

    @property
    def needs_synthesis(self) -> bool:
        return self.intent == "synthesis"


class IntentParser:
    def __init__(self, llm: BaseLLMProvider | None = None) -> None:
        self._llm = llm or NoLLMProvider()

    def parse(self, query: str) -> ParsedIntent:
        tickers = extract_tickers(query)
        macro_series = detect_macro_series(query)
        intent = self._classify_intent(query)

        # Override: if macro series detected but no tickers and intent is
        # ambiguous (e.g. 'quote'), prefer macro intent
        if macro_series and not tickers and intent not in ("macro", "synthesis", "multi"):
            intent = "macro"

        # Extract optional params
        period = extract_period(query)
        interval = extract_interval(query)
        limit = extract_limit(query)

        tasks: list[dict[str, Any]] = []

        # Handle synthesis
        if intent == "synthesis":
            tasks.append({"_intent": "synthesis", "source": "synthesis", "query": query})
            return ParsedIntent(raw_query=query, intent=intent, tickers=tickers,
                                macro_series=macro_series, tasks=tasks)

        # Multi-source detection
        if tickers and macro_series:
            intent = "multi"

        # Build equity tasks
        for ticker in tickers:
            task = self._build_equity_task(ticker, intent, query)
            if period:
                task["period"] = period
            if interval:
                task["interval"] = interval
            if limit:
                task["limit"] = limit
            tasks.append(task)

        # Build macro tasks
        for sid in macro_series:
            tasks.append({"source": "fred", "series_id": sid})

        # Fallback: try LLM parsing if we have no tasks
        llm_assisted = False
        if not tasks and self._llm.is_available and not isinstance(self._llm, NoLLMProvider):
            logger.info("Rule-based parser found no tasks — falling back to LLM parsing")
            tasks = self._llm_parse(query)
            if tasks:
                llm_assisted = True
                logger.info("LLM parser returned %d task(s)", len(tasks))

        parsed = ParsedIntent(
            raw_query=query, intent=intent, tickers=tickers,
            macro_series=macro_series, tasks=tasks,
        )
        if llm_assisted:
            parsed.params["_llm_assisted"] = True
        if "quarterly" in query.lower() or "quarter" in query.lower():
            parsed.params["quarterly"] = True
        if "annual" in query.lower():
            parsed.params["quarterly"] = False
        if period:
            parsed.params["period"] = period
        if interval:
            parsed.params["interval"] = interval
        if limit:
            parsed.params["limit"] = limit

        return parsed

    def _classify_intent(self, query: str) -> str:
        """Classify intent by keyword matching.

        Scoring: each matching keyword adds its character length to the
        intent's score.  This naturally prefers more-specific (longer)
        keyword phrases over short generic ones when counts tie.
        """
        lower = query.lower()
        scores: dict[str, int] = {}
        for intent, keywords in _INTENT_KEYWORDS.items():
            score = sum(len(kw) for kw in keywords if kw in lower)
            if score > 0:
                scores[intent] = score
        return max(scores, key=scores.get) if scores else "quote"

    def _build_equity_task(self, ticker: str, intent: str, query: str) -> dict[str, Any]:
        """Build a task dict for a single ticker.

        SEC-first routing: financials, filings, filing_markdown, and
        company_facts all target ``sec_edgar``.  Market data (quote,
        history) and company info target ``yahoo_finance``.
        """
        lower = query.lower()
        if intent == "filings":
            form = None
            if "10-k" in lower:
                form = "10-K"
            elif "10-q" in lower:
                form = "10-Q"
            elif "8-k" in lower:
                form = "8-K"
            return {"source": "sec_edgar", "ticker": ticker, "action": "filings", "form": form, "limit": 10}
        elif intent == "filing_markdown":
            form = "10-K"
            if "10-q" in lower:
                form = "10-Q"
            elif "8-k" in lower:
                form = "8-K"
            return {"source": "sec_edgar", "ticker": ticker, "action": "filing_markdown", "form": form}
        elif intent == "company_facts":
            return {"source": "sec_edgar", "ticker": ticker, "action": "company_facts"}
        elif intent == "history":
            return {"source": "yahoo_finance", "ticker": ticker, "action": "history", "period": "1y", "interval": "1d"}
        elif intent == "financials":
            # SEC-first: route financial statement queries to SEC EDGAR
            stmt_type = "income"
            if "balance" in lower:
                stmt_type = "balance"
            elif "cash flow" in lower:
                stmt_type = "cash_flow"
            quarterly = "quarterly" in lower or "quarter" in lower
            return {"source": "sec_edgar", "ticker": ticker, "action": "financials",
                    "statement_type": stmt_type, "quarterly": quarterly}
        elif intent == "company_info":
            return {"source": "yahoo_finance", "ticker": ticker, "action": "info"}
        else:
            return {"source": "yahoo_finance", "ticker": ticker, "action": "quote"}

    def _llm_parse(self, query: str) -> list[dict[str, Any]]:
        raw = self._llm.complete(
            system=(
                "You parse financial data queries into structured tasks. "
                "Return a JSON array of tasks. Each task has: "
                "source (yahoo_finance|fred|sec_edgar), and source-specific params. "
                "Return ONLY the JSON array, no markdown."
            ),
            user=query,
            temperature=0,
            max_tokens=500,
        )
        if not raw:
            return []
        try:
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```\w*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            return json.loads(raw)
        except Exception as exc:
            logger.warning("LLM parse failed: %s", exc)
            return []
