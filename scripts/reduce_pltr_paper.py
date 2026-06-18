"""One-off corrective script: sell excess PLTR shares on Alpaca paper account.

The bot accumulated PLTR via repeated BUY/OVERWEIGHT signals before a position
cap was in place.  This script sells the excess down to TARGET_SHARES=3 using
a SELL LIMIT order at the blended cost basis, so the paper account records no
gain or loss from the correction.

Usage (from TradingAgents/ directory):
    python scripts/reduce_pltr_paper.py [--market]

Flags:
    --market   Use SELL MARKET instead of SELL LIMIT (fills immediately,
               records paper P&L at execution price vs $132.29 cost basis)

Requires ALPACA_API_KEY and ALPACA_API_SECRET env vars.
"""

import argparse
import os
import sys
from pathlib import Path

# Allow running from the TradingAgents/ root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

TICKER = "PLTR"
TARGET_SHARES = 3
COST_BASIS = 132.29  # blended entry price (avg of accumulated lots)


def _client():
    from alpaca.trading.client import TradingClient
    key    = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    if not key or not secret:
        sys.exit("Error: ALPACA_API_KEY and ALPACA_API_SECRET must be set.")
    return TradingClient(key, secret, paper=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", action="store_true",
                        help="Use SELL MARKET instead of SELL LIMIT")
    args = parser.parse_args()

    client = _client()

    # -- Fetch live position ------------------------------------------------
    try:
        positions = {str(p.symbol).upper(): p for p in client.get_all_positions()}
    except Exception as exc:
        sys.exit(f"Error fetching positions: {exc}")

    if TICKER not in positions:
        print(f"No {TICKER} position found on Alpaca paper account. Nothing to do.")
        return

    pos = positions[TICKER]
    live_shares = float(pos.qty)
    live_entry  = float(pos.avg_entry_price)

    print(f"Live Alpaca {TICKER}: {live_shares} shares @ ${live_entry:.4f} avg entry")

    shares_to_sell = live_shares - TARGET_SHARES
    if shares_to_sell <= 0:
        print(f"Already at or below {TARGET_SHARES} shares. Nothing to do.")
        return

    print(f"Selling {shares_to_sell:.0f} shares to reach target of {TARGET_SHARES}.")

    # -- Build order --------------------------------------------------------
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    if args.market:
        req = MarketOrderRequest(
            symbol=TICKER,
            qty=shares_to_sell,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order_desc = "SELL MARKET"
    else:
        req = LimitOrderRequest(
            symbol=TICKER,
            qty=shares_to_sell,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            limit_price=round(COST_BASIS, 2),
        )
        order_desc = f"SELL LIMIT @ ${COST_BASIS:.2f} GTC"

    # -- Submit -------------------------------------------------------------
    try:
        order = client.submit_order(req)
    except Exception as exc:
        sys.exit(f"Order submission failed: {exc}")

    print(f"Order placed: {order_desc}")
    print(f"  Order ID : {order.id}")
    print(f"  Status   : {order.status}")
    print()
    print("Next step: update alpaca_portfolio.json position_caps.PLTR to 3")
    print("(already done if you ran this via the plan — otherwise edit manually)")


if __name__ == "__main__":
    main()
