"""Portfolio-level context block injected per ticker so agents reason about the whole."""
from dataclasses import dataclass
from typing import Optional

from .loader import Portfolio, Holding

_SECTOR_MAP: dict[str, str] = {
    "PANW": "Cybersecurity",
    "NVDA": "AI Semiconductors",
    "VRT":  "AI Infrastructure / Cooling",
    "GME":  "Retail (legacy / speculative)",
    "GMEWS":"Warrant",
    "BRO":  "Insurance",
    "BAC":  "Banking",
    "VZ":   "Telecom",
    "MU":   "Memory Semiconductors",
}

_SCALE_TIERS: tuple[tuple[float, str], ...] = (
    (10_000.0,  "Small account (<$10K)"),
    (100_000.0, "Mid-size account ($10K–$100K)"),
)

_SMALL_ACCOUNT_SIZING_NOTE = (
    "> **Position-sizing rule (small account):** Express every BUY recommendation as a whole "
    "number of shares that fits within available cash for a single position — not a percentage "
    "weight. State the dollar cost of the recommended share count. If one share exceeds a "
    "sensible position size, say so explicitly rather than recommending a fractional buy."
)


def _account_scale(cash: float) -> str:
    """Classify account size from available cash for position-sizing guidance."""
    for ceiling, label in _SCALE_TIERS:
        if cash < ceiling:
            return label
    return "Large account (>$100K)"


@dataclass(frozen=True)
class PositionSummary:
    ticker: str
    entry: float
    shares: float
    sector: str
    cost_basis: float
    current: Optional[float]
    weight_pct: Optional[float]   # % of total equity
    unrealized_pct: Optional[float]


def build_context(
    portfolio: Portfolio,
    prices: dict[str, Optional[float]],
    active_ticker: str,
    total_equity: Optional[float] = None,
) -> str:
    """Return a compact markdown block describing the current portfolio state."""
    holdings = [h for h in portfolio.holdings if h.entry and h.entry > 0]
    total_cost = sum(h.entry * h.shares for h in holdings)
    cash = portfolio.cash_balance
    equity = total_equity or total_cost  # fallback: cost basis

    positions: list[PositionSummary] = []
    for h in holdings:
        current = prices.get(h.ticker)
        cost = h.entry * h.shares
        weight = (cost / (equity + cash) * 100) if (equity + cash) > 0 else None
        unr = ((current - h.entry) / h.entry * 100) if current is not None else None
        positions.append(PositionSummary(
            ticker=h.ticker,
            entry=h.entry,
            shares=h.shares,
            sector=_SECTOR_MAP.get(h.ticker, "Unknown"),
            cost_basis=cost,
            current=current,
            weight_pct=weight,
            unrealized_pct=unr,
        ))

    cash_weight = cash / (total_cost + cash) * 100 if (total_cost + cash) > 0 else 0
    scale = _account_scale(cash)

    # Sort by cost basis descending for table
    positions.sort(key=lambda p: p.cost_basis, reverse=True)

    def _pct(v: Optional[float]) -> str:
        return f"{v:+.1f}%" if v is not None else "n/a"

    rows = [f"| {p.ticker} | {p.sector} | {_pct(p.weight_pct)} | {_pct(p.unrealized_pct)} |" for p in positions]

    active_pos = next((p for p in positions if p.ticker == active_ticker), None)
    if active_pos:
        active_note = (
            f"**Current ticker ({active_ticker})** is a current holding: "
            f"{_pct(active_pos.weight_pct)} of account, "
            f"unrealized P&L {_pct(active_pos.unrealized_pct)}."
        )
    elif active_ticker in (t for t in portfolio.watch_list):
        target = portfolio.targets.get(active_ticker)
        active_note = (
            f"**Current ticker ({active_ticker})** is on the watchlist"
            + (f" with a target entry of ${target:.2f}." if target else ".")
        )
    else:
        active_note = f"**Current ticker ({active_ticker})** is under evaluation."

    lines = [
        "## PORTFOLIO CONTEXT",
        f"**Available cash:** ${cash:,.2f} ({cash_weight:.1f}% of account)  |  "
        f"**Account scale:** {scale}  |  "
        f"**Strategy:** {portfolio.strategy}",
        "",
        "| Ticker | Sector | Weight | Unrealized |",
        "|--------|--------|--------|-----------|",
        *rows,
        "",
        active_note,
    ]
    if scale == _SCALE_TIERS[0][1]:  # small account
        lines += ["", _SMALL_ACCOUNT_SIZING_NOTE]
    return "\n".join(lines)
