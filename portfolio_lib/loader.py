import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_REQUIRED_HOLDING_KEYS = {"ticker", "entry", "shares"}


@dataclass(frozen=True)
class Holding:
    ticker: str
    entry: float
    shares: float
    acquired_date: Optional[str] = None


@dataclass(frozen=True)
class Portfolio:
    holdings: tuple[Holding, ...]
    watch_list: tuple[str, ...]
    targets: dict[str, float]
    strategy: str
    cash_balance: float = 0.0


def load_portfolio(path: Path) -> Portfolio:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    holdings = []
    for i, h in enumerate(raw.get("holdings", [])):
        missing = _REQUIRED_HOLDING_KEYS - h.keys()
        if missing:
            raise ValueError(f"Holding[{i}] missing keys: {missing}")
        holdings.append(Holding(
            ticker=h["ticker"],
            entry=float(h["entry"]),
            shares=float(h["shares"]),
            acquired_date=h.get("acquired_date"),
        ))

    return Portfolio(
        holdings=tuple(holdings),
        watch_list=tuple(raw.get("watch_list", [])),
        targets={k: float(v) for k, v in raw.get("targets", {}).items()},
        strategy=raw.get("strategy", ""),
        cash_balance=float(raw.get("cash_balance", 0.0)),
    )


def iter_holdings(portfolio: Portfolio) -> Iterator[Holding]:
    for h in portfolio.holdings:
        if h.entry == 0.0:
            logger.info("Skipping %s — no entry price (warrant/special security)", h.ticker)
            continue
        yield h


def iter_watchlist(portfolio: Portfolio) -> Iterator[tuple[str, Optional[float]]]:
    for ticker in portfolio.watch_list:
        target = portfolio.targets.get(ticker)
        yield ticker, target
