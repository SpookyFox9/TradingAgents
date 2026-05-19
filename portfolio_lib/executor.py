"""Order staging and Alpaca execution layer.

Pending orders are written to pending_orders.json (StockBoy root, gitignored).
Executed trades are appended to Analysis/trade_log.jsonl.

Usage:
    from portfolio_lib.executor import stage_pending_order  # called by analyze_portfolio.py
    from portfolio_lib.executor import get_pending_orders, submit_order, reject_order  # called by approve_trades.py
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ACTIONABLE_BUY = {"BUY", "OVERWEIGHT"}
ACTIONABLE_SELL = {"SELL", "UNDERWEIGHT"}
ACTIONABLE = ACTIONABLE_BUY | ACTIONABLE_SELL

_DEFAULT_POSITION_PCT = 0.10  # suggest 10% of cash per new position by default


def _pending_path(results_dir: Path) -> Path:
    return results_dir.parent / "pending_orders.json"


def _trade_log_path(results_dir: Path) -> Path:
    return results_dir / "trade_log.jsonl"


def _load_orders(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_orders(path: Path, orders: list[dict]) -> None:
    path.write_text(json.dumps(orders, indent=2), encoding="utf-8")


def stage_pending_order(
    ticker: str,
    decision: str,
    cash_balance: float,
    current_price: Optional[float],
    target_price: Optional[float],
    report_path: Optional[Path],
    results_dir: Path,
) -> None:
    """Write a pending order to pending_orders.json if the signal is actionable.

    Skips silently if a pending order already exists for this ticker.
    """
    decision_upper = decision.strip().upper()
    if decision_upper not in ACTIONABLE:
        return

    action = "BUY" if decision_upper in ACTIONABLE_BUY else "SELL"
    price_for_sizing = current_price or target_price or 0.0
    suggested_notional = round(cash_balance * _DEFAULT_POSITION_PCT, 2)
    suggested_shares = (
        round(suggested_notional / price_for_sizing, 4) if price_for_sizing > 0 else 0.0
    )
    limit_price = (target_price if (action == "BUY" and target_price) else current_price)

    order: dict = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(),
        "ticker": ticker,
        "action": action,
        "signal": decision_upper,
        "suggested_shares": suggested_shares,
        "suggested_notional": suggested_notional,
        "limit_price": limit_price,
        "current_price": current_price,
        "expires_at": (
            (datetime.now() + timedelta(days=1))
            .replace(hour=9, minute=30, second=0, microsecond=0)
            .isoformat()
        ),
        "status": "pending",
        "report_path": str(report_path) if report_path else None,
    }

    pending_path = _pending_path(results_dir)
    orders = _load_orders(pending_path)

    if any(o["ticker"] == ticker and o["status"] == "pending" for o in orders):
        logger.info("Pending order already exists for %s — skipping duplicate", ticker)
        return

    orders.append(order)
    _save_orders(pending_path, orders)
    logger.info("Staged %s order for %s (signal: %s)", action, ticker, decision_upper)


def get_pending_orders(results_dir: Path) -> list[dict]:
    """Return all non-expired pending orders."""
    orders = _load_orders(_pending_path(results_dir))
    now = datetime.now()
    return [
        o for o in orders
        if o.get("status") == "pending"
        and datetime.fromisoformat(o["expires_at"]) > now
    ]


def submit_order(
    order: dict,
    shares_override: Optional[float],
    results_dir: Path,
) -> dict:
    """Submit an approved order to Alpaca (paper or live) and persist the result."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    api_secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_API_SECRET must be set at the User level.\n"
            "  [System.Environment]::SetEnvironmentVariable('ALPACA_API_KEY', '...', 'User')"
        )

    paper = os.environ.get("ALPACA_PAPER", "true").strip().lower() != "false"
    qty = shares_override if shares_override is not None else order["suggested_shares"]
    side = OrderSide.BUY if order["action"] == "BUY" else OrderSide.SELL
    limit_price = order.get("limit_price")

    client = TradingClient(api_key, api_secret, paper=paper)

    if limit_price:
        req = LimitOrderRequest(
            symbol=order["ticker"],
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=round(float(limit_price), 2),
        )
    else:
        req = MarketOrderRequest(
            symbol=order["ticker"],
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )

    submitted = client.submit_order(req)
    logger.info(
        "Submitted %s %s to Alpaca (id=%s, paper=%s)",
        order["action"], order["ticker"], submitted.id, paper,
    )

    updated = {
        **order,
        "status": "submitted",
        "shares_executed": float(qty),
        "alpaca_order_id": str(submitted.id),
        "submitted_at": datetime.now().isoformat(),
        "paper": paper,
    }
    _update_order(results_dir, order["id"], updated)
    _append_trade_log(results_dir, updated)
    return updated


def reject_order(order_id: str, results_dir: Path) -> None:
    """Mark a pending order as rejected."""
    _update_order(results_dir, order_id, {
        "status": "rejected",
        "rejected_at": datetime.now().isoformat(),
    })


def _update_order(results_dir: Path, order_id: str, updates: dict) -> None:
    path = _pending_path(results_dir)
    orders = _load_orders(path)
    _save_orders(path, [{**o, **updates} if o["id"] == order_id else o for o in orders])


def _append_trade_log(results_dir: Path, order: dict) -> None:
    log_path = _trade_log_path(results_dir)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(order) + "\n")
