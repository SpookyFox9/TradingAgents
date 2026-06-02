import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_REQUIRED_HOLDING_KEYS = {"ticker", "entry", "shares"}


@dataclass(frozen=True)
class OpenOrder:
    ticker: str
    side: str
    type: str
    shares: float
    price: float = 0.0  # 0.0 for MARKET orders


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
    wash_sale_lockout_until: Optional[str] = None


@dataclass(frozen=True)
class Portfolio:
    holdings: tuple[Holding, ...]
    watch_list: tuple[str, ...]
    targets: dict[str, float]
    strategy: str
    cash_balance: float = 0.0
    open_orders: tuple[OpenOrder, ...] = ()
    position_caps: dict[str, float] = field(default_factory=dict)  # max shares per ticker
    entry_types: dict[str, str] = field(default_factory=dict)  # "pullback" or "breakout" per watchlist ticker


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
            wash_sale_lockout_until=h.get("wash_sale_lockout_until"),
        ))

    open_orders = []
    for o in raw.get("open_orders", []):
        open_orders.append(OpenOrder(
            ticker=o["ticker"],
            side=o["side"],
            type=o["type"],
            shares=float(o["shares"]),
            price=float(o.get("price", 0.0)),
        ))

    return Portfolio(
        holdings=tuple(holdings),
        watch_list=tuple(raw.get("watch_list", [])),
        targets={k: float(v) for k, v in raw.get("targets", {}).items()},
        strategy=raw.get("strategy", ""),
        cash_balance=float(raw.get("cash_balance", 0.0)),
        open_orders=tuple(open_orders),
        position_caps={k: float(v) for k, v in raw.get("position_caps", {}).items()},
        entry_types={k: str(v) for k, v in raw.get("entry_types", {}).items()},
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


def persist_watchlist_additions(
    portfolio_path: Path,
    tickers: list[str],
    targets: Optional[dict[str, float]] = None,
    entry_types: Optional[dict[str, str]] = None,
) -> list[str]:
    """Atomically append new tickers to watch_list in portfolio.json.

    Skips tickers already in watch_list or holdings. Returns the list of
    tickers that were actually added.
    """
    with open(portfolio_path, encoding="utf-8") as f:
        raw = json.load(f)

    existing_watch = set(raw.get("watch_list", []))
    existing_holdings = {h["ticker"] for h in raw.get("holdings", [])}
    added: list[str] = []
    for ticker in tickers:
        if ticker not in existing_watch and ticker not in existing_holdings:
            raw.setdefault("watch_list", []).append(ticker)
            existing_watch.add(ticker)
            added.append(ticker)

    if targets and added:
        raw.setdefault("targets", {}).update(
            {k: v for k, v in targets.items() if k in added}
        )

    if entry_types and added:
        raw.setdefault("entry_types", {}).update(
            {k: v for k, v in entry_types.items() if k in added}
        )

    if added:
        tmp = portfolio_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        os.replace(tmp, portfolio_path)
        logger.info("Persisted %d new watchlist entry(s): %s", len(added), ", ".join(added))

    return added
