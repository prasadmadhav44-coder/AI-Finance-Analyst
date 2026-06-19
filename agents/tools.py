"""
Tools available to the Research Agent.

This is the SINGLE source of truth for ticker/market-data tools. (The
previous version of this project duplicated fetch_price_history in both
pipeline.py and tools.py, with two different signatures — only the copy in
pipeline.py was actually wired into the agent, so tools.py was dead code.
That duplication has been removed.)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger("ai_finance_analyst.tools")

# Tickers are short alphanumeric strings, optionally with a dot/dash suffix
# for exchange class (e.g. BRK-B, RDS.A). Reject anything else before it
# reaches yfinance / gets interpolated into requests.
_TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,9}$")

# Very small in-process TTL cache. This is a single-process Flask app, so a
# dict is sufficient — it avoids hammering Yahoo Finance when the same
# ticker is requested repeatedly in a short window, which is the most common
# cause of yfinance rate-limit errors in production.
_CACHE_TTL_SECONDS = 60
_price_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_factor_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _validate_ticker(ticker: str) -> str:
    ticker = (ticker or "").strip().upper()
    if not ticker or not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


def fetch_price_history(
    ticker: str,
    period: str = "1mo",
    interval: str = "1d",
) -> list[dict[str, Any]]:
    """Fetch recent price history for a stock ticker.

    Returns the last 5 rows as a list of plain dicts (JSON-serializable),
    or an empty list if the ticker is invalid or no data is available.
    This function never raises outward to the agent — tool calls that throw
    are harder for the LLM to recover from than tool calls that return an
    empty/explicit result.
    """
    try:
        ticker = _validate_ticker(ticker)
    except ValueError as exc:
        logger.warning("fetch_price_history rejected input: %s", exc)
        return []

    cache_key = f"{ticker}:{period}:{interval}"
    cached = _price_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        data = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
        )
    except Exception:
        logger.exception("yfinance download failed for %s", ticker)
        return []

    if data is None or data.empty:
        return []

    # yfinance can return a MultiIndex column frame for some inputs; flatten
    # defensively so to_dict() produces clean JSON-safe keys.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = ["_".join(str(c) for c in col if c) for col in data.columns]

    data = data.tail(5).reset_index()
    if "Date" in data.columns:
        data["Date"] = data["Date"].astype(str)

    records = data.to_dict(orient="records")
    _price_cache[cache_key] = (time.time(), records)
    return records


def fetch_growth_stocks(limit: int = 3) -> list[str]:
    """Simple curated growth stock list (MVP-friendly)."""
    universe = ["NVDA", "AMD", "META", "AMZN", "TSLA"]
    limit = max(0, min(int(limit or 0), len(universe)))
    return universe[:limit]


def fetch_stocks_by_factor(style: str = "growth", limit: int = 5) -> list[dict[str, Any]]:
    """Return a small ranked list of tickers for a given investment style,
    with trailing 12-month and 3-month returns. Used for broader
    "what should I look at" style queries rather than single-ticker lookups.
    """
    universes = {
        "growth": ["NVDA", "AMD", "META", "AMZN", "TSLA"],
        "value": ["JNJ", "PG", "KO", "PEP", "XOM"],
        "momentum": ["NVDA", "META", "TSLA", "AVGO", "COST"],
    }
    style = (style or "growth").lower()
    tickers = universes.get(style, universes["growth"])
    limit = max(0, min(int(limit or 0), len(tickers)))

    cache_key = f"{style}:{limit}"
    cached = _factor_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    results: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period="1y")
            close = hist["Close"]
            if len(close) < 61:
                continue

            ret_12m = (close.iloc[-1] / close.iloc[0] - 1) * 100
            ret_3m = (close.iloc[-1] / close.iloc[-60] - 1) * 100

            results.append(
                {
                    "ticker": ticker,
                    "ret_12m": round(float(ret_12m), 2),
                    "ret_3m": round(float(ret_3m), 2),
                }
            )
        except Exception:
            logger.exception("fetch_stocks_by_factor failed for %s", ticker)
            continue

    results = results[:limit]
    _factor_cache[cache_key] = (time.time(), results)
    return results
