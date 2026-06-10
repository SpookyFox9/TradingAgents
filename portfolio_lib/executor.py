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
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ACTIONABLE_BUY = {"BUY", "OVERWEIGHT"}
ACTIONABLE_SELL = {"SELL", "UNDERWEIGHT"}
ACTIONABLE = ACTIONABLE_BUY | ACTIONABLE_SELL

_DEFAULT_POSITION_PCT = 0.10  # fallback sizing — 10% of cash per new position
_SHARE_REC_RE = re.compile(
    r'\*{0,2}Position\s+Sizing\*{0,2}[^:\n]*:\s*(\d+)\s+shares?'
    r'|(?:^|\n)\s*(?:Buy|Enter|Purchase)\s+(\d+)\s+shares?\s+of\s+[A-Z]{1,5}',
    re.IGNORECASE,
)


def _extract_llm_share_recommendation(text: str) -> Optional[float]:
    """Parse the LLM trader plan for an explicit share count.

    Matches '**Position Sizing**: N shares ...' (primary) or
    'Buy N shares of TICKER' (fallback). Returns None if unparseable or
    the count is outside [1, _MAX_AUTO_SHARES].
    """
    if not text:
        return None
    m = _SHARE_REC_RE.search(text)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    try:
        count = int(raw)
        if 1 <= count <= _MAX_AUTO_SHARES:
            return float(count)
    except (ValueError, TypeError):
        pass
    return None


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
    from .io_utils import atomic_write_text
    atomic_write_text(path, json.dumps(orders, indent=2))


def _is_paper() -> bool:
    return os.environ.get("ALPACA_PAPER", "true").strip().lower() != "false"


def _auto_execute_enabled() -> bool:
    """Auto-execute defaults to True on paper, False on live. Override via ALPACA_AUTO_EXECUTE."""
    explicit = os.environ.get("ALPACA_AUTO_EXECUTE", "").strip().lower()
    if explicit in ("true", "1"):
        return True
    if explicit in ("false", "0"):
        return False
    return _is_paper()  # default: auto on paper, manual on live


def _has_alpaca_keys() -> bool:
    return bool(
        os.environ.get("ALPACA_API_KEY", "").strip()
        and os.environ.get("ALPACA_API_SECRET", "").strip()
    )


def stage_pending_order(
    ticker: str,
    decision: str,
    kind: str,
    cash_balance: float,
    current_price: Optional[float],
    target_price: Optional[float],
    report_path: Optional[Path],
    results_dir: Path,
    *,
    portfolio=None,            # portfolio_lib.loader.Portfolio — optional for backwards compat
    alpaca_portfolio_path: Optional[Path] = None,  # if set, update after auto-execute
    prices: Optional[dict] = None,  # current market prices for R5b concentration check
    trader_plan_text: Optional[str] = None,  # LLM trader plan — used to extract share recommendation
) -> Optional[str]:
    """Stage or auto-execute an order depending on paper/live mode.

    Paper + ALPACA_AUTO_EXECUTE=true (default): submits immediately to Alpaca.
    Live or auto-execute disabled: writes to pending_orders.json for manual review.
    Skips silently if a pending order already exists for this ticker.
    SELL/UNDERWEIGHT signals are ignored for WATCHLIST and CANDIDATE tickers
    (no position to sell).

    Returns the compliance rule code (e.g. "R1") if the order was blocked, or
    None if the order was staged / submitted successfully.
    """
    ticker = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,5}", ticker):
        logger.warning("Invalid ticker symbol %r — skipping", ticker)
        return None

    decision_upper = decision.strip().upper()
    if decision_upper not in ACTIONABLE:
        return None

    kind_upper = kind.upper() if isinstance(kind, str) else str(kind).split(".")[-1].upper()
    if decision_upper in ACTIONABLE_SELL and kind_upper in ("WATCHLIST", "CANDIDATE"):
        logger.info("Skipping SELL signal for %s — not a held position (%s)", ticker, kind_upper)
        return None

    action = "BUY" if decision_upper in ACTIONABLE_BUY else "SELL"
    entry_type = (
        portfolio.entry_types.get(ticker, "pullback") if portfolio is not None else "pullback"
    )
    price_for_sizing = current_price or target_price or 0.0
    llm_shares = _extract_llm_share_recommendation(trader_plan_text)
    if llm_shares is not None and price_for_sizing > 0:
        suggested_shares = min(llm_shares, _MAX_AUTO_SHARES)
        suggested_notional = round(suggested_shares * price_for_sizing, 2)
    else:
        suggested_notional = round(cash_balance * _DEFAULT_POSITION_PCT, 2)
        suggested_shares = (
            round(suggested_notional / price_for_sizing, 4) if price_for_sizing > 0 else 0.0
        )
    # pullback: limit at target (wait for the dip to fill); breakout: limit at current price
    limit_price = (
        target_price
        if (action == "BUY" and target_price and entry_type == "pullback")
        else current_price
    )

    # For auto-execute, use Alpaca portfolio state so R5/R6 reflect actual paper-account
    # positions and cash rather than the Fidelity baseline. Doctrine fields (caps, rules)
    # are always inherited from the Fidelity portfolio.
    compliance_portfolio = (
        _build_alpaca_compliance_portfolio(alpaca_portfolio_path, portfolio)
        if (
            portfolio is not None
            and alpaca_portfolio_path is not None
            and _auto_execute_enabled()
            and _has_alpaca_keys()
        )
        else portfolio
    )

    # Compliance pre-flight check
    if compliance_portfolio is not None:
        from portfolio_lib.compliance import check_order
        result = check_order(
            ticker, action, compliance_portfolio,
            trade_log_path=_trade_log_path(results_dir),
            suggested_notional=suggested_notional,
            prices=prices,
        )
        if not result.allowed:
            logger.warning(
                "Compliance block [%s] — %s %s skipped: %s",
                result.rule, action, ticker, result.reason,
            )
            return f"{result.rule}: {result.reason}"
    else:
        logger.warning(
            "stage_pending_order called without portfolio — compliance checks skipped for %s",
            ticker,
        )

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

    if _auto_execute_enabled() and _has_alpaca_keys():
        if _open_same_side_order_exists(ticker, action):
            logger.info(
                "[AUTO] Skipping %s %s — open same-side order already exists on Alpaca",
                action, ticker,
            )
            return None
        try:
            result = submit_order(order, None, results_dir, portfolio=portfolio, prices=prices)
            logger.info(
                "[AUTO] Submitted %s %s to Alpaca paper (signal: %s)",
                action, ticker, decision_upper,
            )
            if alpaca_portfolio_path is not None:
                from portfolio_lib.alpaca_sync import update_after_trade
                update_after_trade(alpaca_portfolio_path, result)
        except Exception as exc:
            logger.warning(
                "[AUTO] Submit failed for %s, falling back to pending queue: %s", ticker, exc
            )
            _stage_to_file(order, results_dir)
        return None

    _stage_to_file(order, results_dir)
    return None


def _stage_to_file(order: dict, results_dir: Path) -> None:
    pending_path = _pending_path(results_dir)
    orders = _load_orders(pending_path)
    if any(o["ticker"] == order["ticker"] and o["status"] == "pending" for o in orders):
        logger.info("Pending order already exists for %s — skipping duplicate", order["ticker"])
        return
    orders.append(order)
    _save_orders(pending_path, orders)
    logger.info("Staged %s order for %s (signal: %s)", order["action"], order["ticker"], order["signal"])


def get_pending_orders(results_dir: Path) -> list[dict]:
    """Return all non-expired pending orders."""
    orders = _load_orders(_pending_path(results_dir))
    now = datetime.now()
    return [
        o for o in orders
        if o.get("status") == "pending"
        and datetime.fromisoformat(o["expires_at"]) > now
    ]


_MAX_AUTO_SHARES = 10  # hard cap per auto-execute order (interactive approve_trades allows up to 500)


def _build_alpaca_compliance_portfolio(alpaca_portfolio_path: Path, fidelity_portfolio):
    """Return a Portfolio for compliance checks when auto-executing on Alpaca paper.

    Uses Alpaca's holdings and cash balance (actual paper-account state) so that
    R5 concentration checks and R6 cash checks reflect what Alpaca actually holds,
    not the Fidelity baseline. Doctrine fields (position_caps, entry_types, tax-loss
    rules) are always taken from the Fidelity portfolio so they stay consistent.

    Falls back to fidelity_portfolio if the Alpaca file does not exist or cannot be
    parsed — this keeps compliance running rather than silently skipping it.
    """
    if not alpaca_portfolio_path.exists():
        return fidelity_portfolio
    try:
        from portfolio_lib.loader import load_portfolio, Portfolio
        alpaca = load_portfolio(alpaca_portfolio_path)
        return Portfolio(
            holdings=alpaca.holdings,
            watch_list=alpaca.watch_list,
            targets=alpaca.targets if alpaca.targets else fidelity_portfolio.targets,
            strategy=alpaca.strategy,
            cash_balance=alpaca.cash_balance,
            open_orders=alpaca.open_orders,
            position_caps=fidelity_portfolio.position_caps,
            entry_types=fidelity_portfolio.entry_types,
        )
    except Exception as exc:
        logger.warning(
            "Could not load Alpaca portfolio for compliance (%s) — using Fidelity portfolio",
            exc,
        )
        return fidelity_portfolio


def _open_same_side_order_exists(ticker: str, action: str) -> bool:
    """Return True if Alpaca already has an open order on the same side for this ticker.

    Used by the auto-execute path to prevent duplicate DAY orders when the pipeline
    runs multiple times in one session (e.g. holdings run at 07:30, watchlist at 09:20).
    Failures are logged and swallowed so a bad API response never silently blocks a
    legitimate first order.
    """
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        client = TradingClient(
            os.environ.get("ALPACA_API_KEY", "").strip(),
            os.environ.get("ALPACA_API_SECRET", "").strip(),
            paper=_is_paper(),
        )
        req = GetOrdersRequest(status="open", symbols=[ticker])
        open_orders = client.get_orders(filter=req)
        same = "buy" if action == "BUY" else "sell"
        return any(
            str(getattr(o, "side", "")).lower().endswith(same)
            for o in open_orders
        )
    except Exception as exc:
        logger.warning(
            "Same-side open-order check failed for %s (%s) — proceeding", ticker, exc
        )
        return False


def _check_conflicting_open_orders(client, ticker: str, action: str) -> None:
    """Raise ValueError if Alpaca already has an open order on the opposite side for this ticker.

    Prevents the 40310000 wash-trade rejection by catching the conflict before the API call.
    Failures in the check itself are logged and swallowed so a bad API response never silently
    blocks a legitimate order.
    """
    try:
        from alpaca.trading.requests import GetOrdersRequest
        req = GetOrdersRequest(status="open", symbols=[ticker])
        open_orders = client.get_orders(filter=req)
    except Exception as exc:
        logger.warning("Open-order pre-check failed for %s (%s) — proceeding without check", ticker, exc)
        return

    opposite = "sell" if action == "BUY" else "buy"
    conflicts = [
        o for o in open_orders
        if str(getattr(o, "side", "")).lower().endswith(opposite)
    ]
    if conflicts:
        ids = ", ".join(str(getattr(o, "id", "?"))[:8] for o in conflicts)
        raise ValueError(
            f"wash-trade pre-check: open {opposite.upper()} order(s) for {ticker} "
            f"({ids}) — blocked to prevent API rejection"
        )


def submit_order(
    order: dict,
    shares_override: Optional[float],
    results_dir: Path,
    *,
    portfolio=None,  # portfolio_lib.loader.Portfolio — optional; enables defense-in-depth check
    prices: Optional[dict] = None,  # current market prices for R5b re-check at submit time
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

    # Defense-in-depth compliance re-check (catches lockout changes since staging)
    if portfolio is not None:
        from portfolio_lib.compliance import check_order
        chk = check_order(
            order["ticker"], order["action"], portfolio,
            trade_log_path=_trade_log_path(results_dir),
            suggested_notional=order.get("suggested_notional"),
            prices=prices,
        )
        if not chk.allowed:
            raise ValueError(
                f"Compliance block [{chk.rule}] at submit time — {order['action']} "
                f"{order['ticker']} rejected: {chk.reason}"
            )

    paper = os.environ.get("ALPACA_PAPER", "true").strip().lower() != "false"
    qty = shares_override if shares_override is not None else order["suggested_shares"]

    # Safety cap: auto-execute path (shares_override=None) must not exceed _MAX_AUTO_SHARES
    if shares_override is None and qty > _MAX_AUTO_SHARES:
        logger.warning(
            "Auto-execute qty %.4f exceeds cap %d for %s — clamped",
            qty, _MAX_AUTO_SHARES, order["ticker"],
        )
        qty = _MAX_AUTO_SHARES

    if qty <= 0:
        raise ValueError(f"Order quantity must be > 0, got {qty} for {order['ticker']}")

    side = OrderSide.BUY if order["action"] == "BUY" else OrderSide.SELL
    limit_price = order.get("limit_price")

    client = TradingClient(api_key, api_secret, paper=paper)
    _check_conflicting_open_orders(client, order["ticker"], order["action"])

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
