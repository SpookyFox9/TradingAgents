import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_REQUIRED_HOLDING_KEYS = {"ticker", "entry", "shares"}


@dataclass(frozen=True)
class OpenOrder:
    ticker: str
    side: str
    type: str
    price: float
    shares: float


@dataclass(frozen=True)
class Holding:
    ticker: str
    entry: float
    shares: float
    acquired_date: Optional[str] = None
    role: Optional[str] = None
    harvest_target_date: Optional[str] = None
    lot_method: Optional[str] = None
    wash_sale_lockout_days: Optional[int] = None


@dataclass(frozen=True)
class Portfolio:
    holdings: tuple[Holding, ...]
    watch_list: tuple[str, ...]
    targets: dict[str, float]
    strategy: str
    cash_balance: float = 0.0
    open_orders: tuple[OpenOrder, ...] = ()


def load_portfolio(path: Path) -> Portfolio:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    holdings = []
    for i, h in enumerate(raw.get("holdings", [])):
        missing = _REQUIRED_HOLDING_KEYS - h.keys()
        if missing:
            raise ValueError(f"Holding[{i}] missing keys: {missing}")
        lockout = h.get("wash_sale_lockout_days")
        holdings.append(Holding(
            ticker=h["ticker"],
            entry=float(h["entry"]),
            shares=float(h["shares"]),
            acquired_date=h.get("acquired_date"),
            role=h.get("role"),
            harvest_target_date=h.get("harvest_target_date"),
            lot_method=h.get("lot_method"),
            wash_sale_lockout_days=int(lockout) if lockout is not None else None,
        ))

    open_orders = []
    for o in raw.get("open_orders", []):
        open_orders.append(OpenOrder(
            ticker=o["ticker"],
            side=o["side"],
            type=o["type"],
            price=float(o["price"]),
            shares=float(o["shares"]),
        ))

    return Portfolio(
        holdings=tuple(holdings),
        watch_list=tuple(raw.get("watch_list", [])),
        targets={k: float(v) for k, v in raw.get("targets", {}).items()},
        strategy=raw.get("strategy", ""),
        cash_balance=float(raw.get("cash_balance", 0.0)),
        open_orders=tuple(open_orders),
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
