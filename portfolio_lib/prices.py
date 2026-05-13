"""Live price lookup via yfinance. Returns None on any failure — callers must handle."""

import logging
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

_cache: dict[str, Optional[float]] = {}


def get_price(ticker: str) -> Optional[float]:
    if ticker in _cache:
        return _cache[ticker]

    try:
        info = yf.Ticker(ticker).fast_info
        price = float(info["last_price"])
        _cache[ticker] = price
        return price
    except Exception as exc:
        logger.warning("Could not fetch price for %s: %s", ticker, exc)
        _cache[ticker] = None
        return None


def clear_cache() -> None:
    _cache.clear()
