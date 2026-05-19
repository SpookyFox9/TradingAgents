"""
test_alpaca_orders.py — Verify common Alpaca order types work against the paper account.

Tests: market buy, limit buy, limit sell, stop-loss, stop-limit, trailing stop, market sell.
All non-filling orders are cancelled at the end. Net position change = 0.

Usage:
    python test_alpaca_orders.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
    TrailingStopOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce

TICKER = "GME"
POSITION_QTY = 5       # shares to buy upfront (covers all sell-side tests)
SELL_QTY_EACH = 1      # 1 share per sell order type (4 types × 1 = 4 covered by position)


def _build_client() -> tuple[TradingClient, bool]:
    key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"
    if not key or not secret:
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_API_SECRET must be set at User level.\n"
            "  [System.Environment]::SetEnvironmentVariable('ALPACA_API_KEY', '...', 'User')"
        )
    return TradingClient(key, secret, paper=paper), paper


def _describe(order) -> str:
    parts = [f"id={str(order.id)[:8]}", f"status={order.status}"]
    if getattr(order, "limit_price", None):
        parts.append(f"lim=${float(order.limit_price):.2f}")
    if getattr(order, "stop_price", None):
        parts.append(f"stop=${float(order.stop_price):.2f}")
    if getattr(order, "trail_percent", None):
        parts.append(f"trail={float(order.trail_percent):.1f}%")
    if getattr(order, "filled_avg_price", None):
        parts.append(f"filled@${float(order.filled_avg_price):.2f}")
    return "  ".join(parts)


def _wait_for_fill(client: TradingClient, order_id, timeout: int = 10):
    for _ in range(timeout):
        o = client.get_order_by_id(order_id)
        if o.status == "filled":
            return o
        time.sleep(1)
    return client.get_order_by_id(order_id)


def _cancel_orders(client: TradingClient, order_ids: list[str]) -> int:
    cancelled = 0
    terminal = {"filled", "cancelled", "expired", "done_for_day"}
    for oid in order_ids:
        try:
            o = client.get_order_by_id(oid)
            if str(o.status) not in terminal:
                client.cancel_order_by_id(oid)
                print(f"    [cancelled] {str(oid)[:8]}")
                cancelled += 1
            else:
                print(f"    [skip {o.status}] {str(oid)[:8]}")
        except Exception as exc:
            print(f"    [cancel failed] {str(oid)[:8]}: {exc}")
    return cancelled


def main() -> None:
    client, paper = _build_client()
    mode = "PAPER" if paper else "LIVE"
    print(f"\n{'='*60}")
    print(f"  Alpaca Order Type Test — {mode}")
    print(f"{'='*60}")

    acct = client.get_account()
    print(f"  Cash: ${float(acct.cash):,.2f}  |  Buying power: ${float(acct.buying_power):,.2f}\n")

    test_ids: list[str] = []

    # ── 1. Market BUY (establish position for sell-side tests) ────────────────
    print(f"[1] Market BUY  {POSITION_QTY}x {TICKER}")
    order = client.submit_order(MarketOrderRequest(
        symbol=TICKER, qty=POSITION_QTY, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
    ))
    order = _wait_for_fill(client, order.id)
    print(f"    {_describe(order)}\n")
    test_ids.append(str(order.id))

    # ── Sell-side orders (no open BUY orders — avoids wash-trade guard) ────────
    # Alpaca blocks SELL stops whenever any open BUY limit exists, regardless of
    # price distance. Test sell-side orders first, then test limit BUY separately.

    # ── 2. Limit SELL (above market — queues, won't fill) ─────────────────────
    print(f"[2] Limit SELL  1x {TICKER} @ $50.00  (above market, GTC)")
    order = client.submit_order(LimitOrderRequest(
        symbol=TICKER, qty=SELL_QTY_EACH, side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC, limit_price=50.00,
    ))
    print(f"    {_describe(order)}\n")
    test_ids.append(str(order.id))

    # ── 3. Stop-loss SELL (stop below market — won't trigger) ─────────────────
    print(f"[3] Stop-loss   1x {TICKER} stop=$10.00  (GTC)")
    order = client.submit_order(StopOrderRequest(
        symbol=TICKER, qty=SELL_QTY_EACH, side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC, stop_price=10.00,
    ))
    print(f"    {_describe(order)}\n")
    test_ids.append(str(order.id))

    # ── 4. Stop-limit SELL (stop + limit floor) ───────────────────────────────
    print(f"[4] Stop-limit  1x {TICKER} stop=$10.00 / limit=$9.50  (GTC)")
    order = client.submit_order(StopLimitOrderRequest(
        symbol=TICKER, qty=SELL_QTY_EACH, side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC, stop_price=10.00, limit_price=9.50,
    ))
    print(f"    {_describe(order)}\n")
    test_ids.append(str(order.id))

    # ── 5. Trailing stop SELL (5% trail) ──────────────────────────────────────
    print(f"[5] Trailing    1x {TICKER} trail=5%  (GTC)")
    order = client.submit_order(TrailingStopOrderRequest(
        symbol=TICKER, qty=SELL_QTY_EACH, side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC, trail_percent=5.0,
    ))
    print(f"    {_describe(order)}\n")
    test_ids.append(str(order.id))

    # ── Cancel sell-side orders, then close position ───────────────────────────
    print(f"{'-'*60}")
    print("  Cancelling sell-side orders...")
    n = _cancel_orders(client, test_ids)
    print(f"  Cancelled {n} order(s).\n")
    test_ids.clear()

    # ── 6. Market SELL (close position) ───────────────────────────────────────
    print(f"[6] Market SELL {POSITION_QTY}x {TICKER}  (close position)")
    order = client.submit_order(MarketOrderRequest(
        symbol=TICKER, qty=POSITION_QTY, side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
    ))
    order = _wait_for_fill(client, order.id)
    print(f"    {_describe(order)}\n")

    # ── 7. Limit BUY (tested separately — no position, no open sell orders) ───
    # Must be after position is closed to avoid wash-trade conflict with stops.
    print(f"[7] Limit BUY   1x {TICKER} @ $5.00  (below market, GTC)")
    order = client.submit_order(LimitOrderRequest(
        symbol=TICKER, qty=1, side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC, limit_price=5.00,
    ))
    print(f"    {_describe(order)}\n")
    test_ids.append(str(order.id))

    print(f"{'-'*60}")
    print("  Cancelling limit BUY...")
    n = _cancel_orders(client, test_ids)
    print(f"  Cancelled {n} order(s).\n")

    acct = client.get_account()
    print(f"{'='*60}")
    print(f"  All done. Cash after test: ${float(acct.cash):,.2f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
