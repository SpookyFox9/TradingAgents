"""Interactive approval CLI for staged pending orders.

Usage:
    python approve_trades.py [--portfolio PATH] [--dry-run]

Or via the wrapper:
    .\\approve_trade.ps1 [--dry-run]
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_lib.config import RunConfig
from portfolio_lib.executor import get_pending_orders, submit_order, reject_order


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve or reject staged trade orders.")
    parser.add_argument("--portfolio", metavar="PATH", help="Path to portfolio.json")
    parser.add_argument("--dry-run", action="store_true", help="Show orders without submitting")
    args = parser.parse_args()

    portfolio_path = (
        Path(args.portfolio)
        if args.portfolio
        else Path(__file__).resolve().parent.parent / "portfolio.json"
    )
    run_cfg = RunConfig.default(portfolio_path=portfolio_path)
    orders = get_pending_orders(run_cfg.results_dir)

    if not orders:
        print("\nNo pending orders.\n")
        return

    paper = os.environ.get("ALPACA_PAPER", "true").strip().lower() != "false"
    mode = "PAPER" if paper else "LIVE"
    if args.dry_run:
        mode += " [DRY RUN — no orders will be submitted]"

    print(f"\nPending orders ({len(orders)}) — Mode: {mode}")
    print("─" * 64)
    for i, o in enumerate(orders, 1):
        created = o["created_at"][:16].replace("T", " ")
        expires = o["expires_at"][:16].replace("T", " ")
        price_str = f"@ limit ${o['limit_price']:.2f}" if o.get("limit_price") else "@ market"
        notional = o.get("suggested_notional", 0)
        shares = o.get("suggested_shares", 0)
        print(
            f"\n  [{i}] {o['action']} {o['ticker']}  ~{shares:.2f} sh {price_str}  "
            f"(~${notional:.0f})"
        )
        print(f"      Signal: {o['signal']}  |  Created: {created}  |  Expires: {expires}")
        if o.get("report_path"):
            print(f"      Report:  {o['report_path']}")
    print("\n" + "─" * 64)
    print("  Commands: y=approve  n=reject  shares=N=custom qty  q=quit\n")

    for i, order in enumerate(orders, 1):
        if args.dry_run:
            print(f"  [{i}] {order['action']} {order['ticker']} — dry run, skipping")
            continue

        while True:
            raw = input(
                f"  [{i}] {order['action']} {order['ticker']}  approve? "
            ).strip().lower()

            if raw == "q":
                print("\nExiting — remaining orders stay pending until expiry.")
                return

            if raw == "y":
                result = submit_order(order, None, run_cfg.results_dir)
                alpaca_id = result.get("alpaca_order_id", "n/a")
                print(f"      Submitted — Alpaca id: {alpaca_id}\n")
                break

            if raw == "n":
                reject_order(order["id"], run_cfg.results_dir)
                print(f"      Rejected.\n")
                break

            if raw.startswith("shares="):
                try:
                    qty = float(raw.split("=", 1)[1])
                    result = submit_order(order, qty, run_cfg.results_dir)
                    alpaca_id = result.get("alpaca_order_id", "n/a")
                    print(f"      Submitted {qty} shares — Alpaca id: {alpaca_id}\n")
                    break
                except (ValueError, IndexError):
                    print("      Format: shares=3.5\n")
                    continue

            print("      Enter y, n, shares=N, or q\n")

    print("Done.\n")


if __name__ == "__main__":
    main()
