"""Pre-trade regulatory compliance checks for a US retail cash account.

Rule codes
----------
R1  Wash-sale lockout   — block BUY within 30-day window after a same-ticker loss sell
R2  Tax-loss harvest    — block BUY *and* SELL on intentional harvest-hold positions
R3  Warrant             — block all sides on warrants (zero entry or W/WS/WT suffix)
R4  No-position SELL    — block SELL when ticker is not in holdings (prevents short)
R5  Position cap        — block BUY pushing a single ticker above 30 % of portfolio
R6  Cash settlement     — block BUY whose notional exceeds settled cash (T+1, cash account)

PDT (Pattern Day Trader) rule is NOT enforced — this is a cash account; PDT applies
only to margin accounts.

All checks are soft-blocks: ComplianceResult(allowed=False, ...) is returned and the
caller decides the response (log + skip is standard behaviour).
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_POSITION_PCT = 0.30
_WARRANT_SUFFIX_RE = re.compile(r"^[A-Z]{1,4}(W|WS|WT)$")


@dataclass(frozen=True)
class ComplianceResult:
    allowed: bool
    rule: str    # "" when allowed
    reason: str  # "" when allowed


def _is_warrant(ticker: str, entry: float) -> bool:
    return entry == 0.0 or bool(_WARRANT_SUFFIX_RE.match(ticker))


def _unsettled_sell_proceeds(trade_log_path: Path, today: date) -> float:
    """Sum proceeds of SELL orders from the last 1 business day (T+1 cash settlement).

    Holiday calendar is intentionally omitted — market holidays are treated as
    settled, making the check slightly conservative (safe to under-block).
    """
    if not trade_log_path.exists():
        return 0.0
    prev = today - timedelta(days=1)
    while prev.weekday() >= 5:  # skip Saturday=5, Sunday=6
        prev -= timedelta(days=1)
    proceeds = 0.0
    try:
        for line in trade_log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                order = json.loads(line)
                if order.get("action") != "SELL":
                    continue
                submitted_at = order.get("submitted_at", "")
                if not submitted_at:
                    continue
                order_date = date.fromisoformat(submitted_at[:10])
                if order_date >= prev:
                    shares = float(order.get("shares_executed", 0))
                    price = float(
                        order.get("limit_price") or order.get("current_price") or 0
                    )
                    proceeds += shares * price
            except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                continue
    except OSError:
        pass
    return proceeds


def check_order(
    ticker: str,
    action: str,
    portfolio,  # portfolio_lib.loader.Portfolio — not typed to avoid circular import
    *,
    today: Optional[date] = None,
    trade_log_path: Optional[Path] = None,
    suggested_notional: Optional[float] = None,
) -> ComplianceResult:
    """Return ComplianceResult indicating whether the order is compliant.

    Parameters
    ----------
    ticker:             Ticker symbol (upper-cased internally).
    action:             "BUY" or "SELL" (upper-cased internally).
    portfolio:          Loaded Portfolio object.
    today:              Override today's date — used in tests.
    trade_log_path:     Path to Analysis/trade_log.jsonl; required for R6.
    suggested_notional: Dollar notional of the proposed order; required for R6.
    """
    today = today or date.today()
    ticker = ticker.upper()
    action = action.upper()

    holdings_map = {h.ticker.upper(): h for h in portfolio.holdings}
    holding = holdings_map.get(ticker)

    # R3 — Warrant: zero entry price or ticker ending in W / WS / WT
    if holding and _is_warrant(ticker, holding.entry):
        return ComplianceResult(
            False, "R3", f"{ticker} is a warrant — all order sides blocked"
        )
    if _WARRANT_SUFFIX_RE.match(ticker):
        return ComplianceResult(
            False, "R3", f"{ticker} matches warrant suffix pattern — blocked"
        )

    # R2 — Tax-loss harvest hold: block BUY and SELL
    if holding and holding.role == "tax-loss-harvest":
        return ComplianceResult(
            False, "R2",
            f"{ticker} is a tax-loss-harvest hold — BUY and SELL blocked; "
            "output tax-loss status only",
        )

    # R1 — Wash-sale lockout: block BUY within the active window
    if action == "BUY" and holding and holding.wash_sale_lockout_until:
        try:
            lockout_until = date.fromisoformat(holding.wash_sale_lockout_until)
            if today <= lockout_until:
                return ComplianceResult(
                    False, "R1",
                    f"{ticker} wash-sale lockout active until {lockout_until} "
                    f"(today: {today}) — BUY blocked",
                )
        except ValueError:
            logger.warning(
                "Unparseable wash_sale_lockout_until for %s: %r — R1 skipped",
                ticker, holding.wash_sale_lockout_until,
            )

    # R4 — No-position SELL guard: prevent accidental short
    if action == "SELL":
        has_position = holding is not None and holding.shares > 0
        if not has_position:
            return ComplianceResult(
                False, "R4",
                f"{ticker} SELL blocked — no open position (would create a short)",
            )

    # R5 — Position cap: block BUY at or above explicit share cap OR 30% portfolio weight
    if action == "BUY" and holding and holding.entry > 0:
        # R5a — explicit per-ticker share cap from portfolio.position_caps
        share_cap = getattr(portfolio, "position_caps", {}).get(ticker)
        if share_cap is not None and holding.shares >= share_cap:
            return ComplianceResult(
                False, "R5",
                f"{ticker} at max position ({holding.shares:.0f}/{share_cap:.0f} sh) — BUY blocked",
            )
        # R5b — portfolio concentration cap (30% of total value)
        cost_basis = sum(h.entry * h.shares for h in portfolio.holdings if h.entry > 0)
        portfolio_value = portfolio.cash_balance + cost_basis
        if portfolio_value > 0:
            current_weight = (holding.entry * holding.shares) / portfolio_value
            if current_weight >= _MAX_POSITION_PCT:
                return ComplianceResult(
                    False, "R5",
                    f"{ticker} position cap reached — current weight "
                    f"{current_weight:.1%} ≥ {_MAX_POSITION_PCT:.0%} of portfolio "
                    f"(${portfolio_value:,.0f}) — BUY blocked",
                )

    # R6 — T+1 cash settlement: block BUY exceeding settled cash (cash account only)
    if (
        action == "BUY"
        and trade_log_path is not None
        and suggested_notional is not None
    ):
        unsettled = _unsettled_sell_proceeds(trade_log_path, today)
        settled_cash = portfolio.cash_balance - unsettled
        if suggested_notional > settled_cash:
            return ComplianceResult(
                False, "R6",
                f"{ticker} BUY blocked — notional ${suggested_notional:,.2f} exceeds "
                f"settled cash ${settled_cash:,.2f} "
                f"(${unsettled:,.2f} unsettled from recent sells; T+1 rule)",
            )

    return ComplianceResult(True, "", "")


def is_blocked_ticker(ticker: str, portfolio=None) -> bool:
    """Lightweight check used by the discovery filter.

    Without a portfolio, only the warrant-suffix pattern is checked.
    With a portfolio, also catches tax-loss holds and active wash-sale lockouts.
    """
    ticker = ticker.upper()
    if _WARRANT_SUFFIX_RE.match(ticker):
        return True
    if portfolio is None:
        return False
    holdings_map = {h.ticker.upper(): h for h in portfolio.holdings}
    holding = holdings_map.get(ticker)
    if holding is None:
        return False
    if _is_warrant(ticker, holding.entry):
        return True
    if holding.role == "tax-loss-harvest":
        return True
    if holding.wash_sale_lockout_until:
        try:
            if date.today() <= date.fromisoformat(holding.wash_sale_lockout_until):
                return True
        except ValueError:
            pass
    return False
