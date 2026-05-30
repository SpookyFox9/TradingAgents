"""Alpaca paper account synchronisation with Fidelity portfolio.json.

Two-portfolio tracking:
  portfolio.json         — Fidelity (real money, manually maintained)
  alpaca_portfolio.json  — Alpaca paper (bot trades, auto-maintained)

Both files share the portfolio.json JSON schema so portfolio_lib.loader
can load either one unchanged.  Doctrine fields (role, wash_sale_lockout_until,
etc.) copied from portfolio.json ensure compliance rules enforce correctly on
the paper account.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DOCTRINE_ROLES = frozenset({"tax-loss-harvest"})


def _is_tradeable(holding: dict) -> bool:
    """True if the holding should be bought on Alpaca paper during a reset."""
    if float(holding.get("entry", 0.0)) == 0.0:
        return False
    if holding.get("role") in _DOCTRINE_ROLES:
        return False
    return True


def _alpaca_paper_client():
    from alpaca.trading.client import TradingClient
    api_key    = os.environ.get("ALPACA_API_KEY", "").strip()
    api_secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_API_SECRET must be set as User env vars."
        )
    return TradingClient(api_key, api_secret, paper=True)


def reset_to_fidelity(
    fidelity_path: Path,
    alpaca_portfolio_path: Path,
    *,
    dry_run: bool = False,
) -> None:
    """Wipe the Alpaca paper account and reset it to the Fidelity baseline.

    1. Cancel all open paper orders.
    2. Close all paper positions (market sell).
    3. Place market BUY orders for each tradeable Fidelity holding (NVDA, etc.).
    4. Write alpaca_portfolio.json as a copy of portfolio.json.

    Doctrine-only holdings (tax-loss-harvest, warrants entry=0) are included in
    alpaca_portfolio.json for compliance enforcement but are NOT purchased on Alpaca.
    """
    fidelity_data = json.loads(fidelity_path.read_text(encoding="utf-8"))
    holdings  = fidelity_data.get("holdings", [])
    tradeable = [h for h in holdings if _is_tradeable(h)]

    if not dry_run:
        client = _alpaca_paper_client()

        try:
            client.cancel_orders()
            logger.info("Alpaca paper reset: all open orders cancelled")
        except Exception as exc:
            logger.warning("Could not cancel Alpaca paper orders: %s", exc)

        try:
            client.close_all_positions(cancel_orders=True)
            logger.info("Alpaca paper reset: all positions closed")
        except Exception as exc:
            logger.warning("Could not close Alpaca paper positions: %s", exc)

        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        for h in tradeable:
            ticker = h["ticker"]
            shares = float(h.get("shares", 0.0))
            if shares <= 0:
                continue
            try:
                req = MarketOrderRequest(
                    symbol=ticker,
                    qty=shares,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
                order = client.submit_order(req)
                logger.info(
                    "Reset: placed market BUY %s × %.2f sh (Alpaca id=%s)",
                    ticker, shares, order.id,
                )
            except Exception as exc:
                logger.warning("Reset: failed to place BUY for %s: %s", ticker, exc)

    now_iso = datetime.now().isoformat()
    alpaca_data = {
        **fidelity_data,
        "last_updated": now_iso[:10],
        "_alpaca_reset_at": now_iso,
        "_source": "auto-synced from portfolio.json via reset_to_fidelity()",
    }

    if dry_run:
        logger.info(
            "[DRY RUN] reset_to_fidelity: would write alpaca_portfolio.json "
            "with %d holdings, cash $%.2f",
            len(holdings), float(fidelity_data.get("cash_balance", 0.0)),
        )
        return

    alpaca_portfolio_path.write_text(
        json.dumps(alpaca_data, indent=2), encoding="utf-8"
    )
    logger.info(
        "alpaca_portfolio.json written — %d holdings, cash $%.2f",
        len(holdings), float(fidelity_data.get("cash_balance", 0.0)),
    )


def load_alpaca_portfolio(
    alpaca_portfolio_path: Path,
    fidelity_path: Path,
) -> dict:
    """Return the raw JSON dict from alpaca_portfolio.json.

    If the file does not exist, initialises it silently from portfolio.json
    (no Alpaca API calls).
    """
    if not alpaca_portfolio_path.exists():
        logger.info(
            "alpaca_portfolio.json not found — initialising from portfolio.json"
        )
        fidelity_data = json.loads(fidelity_path.read_text(encoding="utf-8"))
        alpaca_data = {
            **fidelity_data,
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "_source": "auto-initialised from portfolio.json",
        }
        alpaca_portfolio_path.write_text(
            json.dumps(alpaca_data, indent=2), encoding="utf-8"
        )
        return alpaca_data

    return json.loads(alpaca_portfolio_path.read_text(encoding="utf-8"))


def update_after_trade(alpaca_portfolio_path: Path, order: dict) -> None:
    """Update alpaca_portfolio.json after a successful auto-execute trade.

    BUY:  add or increase holding with weighted-average entry; deduct notional from cash.
    SELL: reduce or remove holding; credit notional to cash.
    """
    if not alpaca_portfolio_path.exists():
        logger.warning(
            "update_after_trade: alpaca_portfolio.json not found — skipping"
        )
        return

    data     = json.loads(alpaca_portfolio_path.read_text(encoding="utf-8"))
    holdings = list(data.get("holdings", []))

    ticker   = order["ticker"]
    action   = order["action"]
    shares   = float(order.get("shares_executed", order.get("suggested_shares", 0.0)))
    price    = float(order.get("limit_price") or order.get("current_price") or 0.0)
    notional = (
        round(shares * price, 2) if price
        else float(order.get("suggested_notional", 0.0))
    )
    cash     = float(data.get("cash_balance", 0.0))

    if action == "BUY":
        existing = next((h for h in holdings if h["ticker"] == ticker), None)
        if existing:
            old_sh  = float(existing.get("shares", 0.0))
            old_ent = float(existing.get("entry", price))
            new_sh  = round(old_sh + shares, 4)
            new_ent = (
                round((old_sh * old_ent + shares * price) / new_sh, 4)
                if (new_sh and price) else old_ent
            )
            existing["shares"] = new_sh
            existing["entry"]  = new_ent
        else:
            holdings.append({
                "ticker":        ticker,
                "entry":         round(price, 4),
                "shares":        shares,
                "acquired_date": order.get("submitted_at", datetime.now().isoformat())[:10],
            })
        data["cash_balance"] = round(cash - notional, 2)

    elif action == "SELL":
        existing = next((h for h in holdings if h["ticker"] == ticker), None)
        if existing:
            new_sh = round(float(existing.get("shares", 0.0)) - shares, 4)
            if new_sh <= 0:
                holdings.remove(existing)
            else:
                existing["shares"] = new_sh
        data["cash_balance"] = round(cash + notional, 2)

    data["holdings"]     = holdings
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")

    alpaca_portfolio_path.write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    logger.info(
        "alpaca_portfolio.json: %s %s %.4f sh @ $%.2f → cash $%.2f",
        action, ticker, shares, price, data["cash_balance"],
    )
