"""
Portfolio analysis entry point.

Usage:
    python analyze_portfolio.py                              # all holdings (quality analyst set)
    python analyze_portfolio.py --tickers NVDA PANW         # specific holdings
    python analyze_portfolio.py --watchlist                  # all watchlist tickers
    python analyze_portfolio.py --watchlist --tickers BRO   # specific watchlist tickers
    python analyze_portfolio.py --av                        # enable Alpha Vantage (burns daily quota)
    python analyze_portfolio.py --deep                      # 2x debate rounds + medium reasoning effort
    python analyze_portfolio.py --analyst-set full          # all 4 analysts incl. social
    python analyze_portfolio.py --analyst-set fast          # market + fundamentals only
    python analyze_portfolio.py --dry-run                   # validate config without running LLM
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _check_env() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print(
            "\nERROR: ANTHROPIC_API_KEY is not set.\n"
            "  Set it as a Windows User environment variable:\n"
            '  [System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")\n'
            "  Then re-open your terminal and rerun.\n"
        )
        sys.exit(1)


def _build_extra_context(portfolio, prices: dict, active_ticker: str, macro_snapshot) -> str:
    """Compose the persona + portfolio + macro block for a specific ticker."""
    from portfolio_lib.persona import render as render_persona
    from portfolio_lib.portfolio_context import build_context
    from portfolio_lib.macro import render as render_macro

    parts = [
        render_persona(),
        "",
        build_context(portfolio, prices, active_ticker),
        "",
        render_macro(macro_snapshot),
    ]
    return "\n".join(parts)


def _print_tax_loss_status(holding, portfolio, prices: dict) -> None:
    """Print tax-loss harvest status for GME-role holdings instead of running the LLM pipeline."""
    import datetime

    from portfolio_lib.prices import get_price

    current_price = prices.get(holding.ticker) or get_price(holding.ticker)
    if not current_price:
        print(f"\n{holding.ticker} [TAX-LOSS HARVEST] — could not fetch current price\n")
        return

    entry = holding.entry
    shares = holding.shares
    loss_per_share = current_price - entry
    total_unrealized = loss_per_share * shares
    loss_pct = (loss_per_share / entry) * 100

    today = datetime.date.today()
    harvest_start = datetime.date(2026, 11, 15)
    harvest_end = datetime.date(2026, 12, 20)
    in_window = harvest_start <= today <= harvest_end

    gme_orders = [o for o in portfolio.open_orders if o.ticker == holding.ticker]

    spike_detected = False
    low_30d = 0.0
    try:
        import yfinance as yf
        hist = yf.Ticker(holding.ticker).history(period="30d")
        if not hist.empty:
            low_30d = float(hist["Low"].min())
            spike_detected = current_price > 2.0 * low_30d
    except Exception:
        pass

    print(f"\n{'='*60}")
    print(f"{holding.ticker}  [TAX-LOSS HARVEST] — intentional hold, no trade signal")
    print("=" * 60)
    print(f"  Current price  : ${current_price:.2f}")
    print(f"  Entry price    : ${entry:.2f}")
    print(f"  Unrealized P/L : ${total_unrealized:,.2f}  ({loss_pct:.1f}%)")
    print(f"  Shares held    : {shares:.0f}")
    if holding.harvest_target_date:
        print(f"  Harvest target : {holding.harvest_target_date}")
    lot_note = holding.lot_method or "highest-cost-first"
    print(f"  Lot method     : {lot_note} (confirm at broker)")
    lockout = holding.wash_sale_lockout_days or 30
    print(f"  Wash-sale rule : {lockout}-day re-entry lockout after any fill")
    harvest_status = "ACTIVE — execute harvest this month" if in_window else f"not yet (opens {harvest_start})"
    print(f"  Harvest window : Nov 15 – Dec 20 2026 [{harvest_status}]")
    if gme_orders:
        for o in gme_orders:
            print(f"  Open order     : {o.shares:.0f}sh {o.side} {o.type} @ ${o.price:.2f}")
    else:
        print(f"  Open orders    : none — consider placing harvest limit order")
    if spike_detected and low_30d:
        print(f"\n  *** SPIKE ALERT: ${current_price:.2f} is >2x the 30-day low (${low_30d:.2f}).")
        print(f"      Consider harvesting remaining shares early at reduced loss.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run multi-agent analysis on your portfolio.")
    parser.add_argument("--tickers", nargs="+", metavar="TICKER", help="Limit to these tickers")
    parser.add_argument("--watchlist", action="store_true", help="Analyze watchlist instead of holdings")
    parser.add_argument("--av", action="store_true", help="Enable Alpha Vantage for technical indicators (burns 25/day quota)")
    parser.add_argument("--deep", action="store_true", help="Double debate rounds + medium reasoning effort (~30%% more tokens)")
    parser.add_argument(
        "--analyst-set",
        choices=["full", "quality", "fast"],
        default="quality",
        dest="analyst_set",
        help="Analyst preset: full (all 4), quality (market+news+fundamentals), fast (market+fundamentals)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Load config and portfolio without running LLM")
    parser.add_argument(
        "--discover", type=int, metavar="N",
        help="Discover N new GARP candidates via two-pass AI infra screen (e.g. --discover 3)",
    )
    parser.add_argument(
        "--discover-only", action="store_true", dest="discover_only",
        help="Skip holdings/watchlist analysis; run discovery pass only (requires --discover N)",
    )
    parser.add_argument(
        "--portfolio", metavar="PATH",
        help="Path to portfolio.json (default: portfolio.json in the TradingAgents directory)",
    )
    parser.add_argument(
        "--reset-alpaca", action="store_true", dest="reset_alpaca",
        help="Wipe the Alpaca paper account and reset it to the current Fidelity baseline, then exit",
    )
    args = parser.parse_args()

    if args.discover_only and not args.discover:
        parser.error("--discover-only requires --discover N (e.g. --discover-only --discover 3)")

    if args.av:
        logger.warning("Alpha Vantage enabled — technical indicator calls will count toward 25/day quota.")
    if args.deep:
        logger.warning("Deep mode enabled -- approx. 30%% more tokens per ticker (2x debate rounds).")

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from portfolio_lib.config import RunConfig
    from portfolio_lib.loader import load_portfolio, iter_holdings, iter_watchlist
    from portfolio_lib.analyzer import analyze_ticker, TickerKind
    from portfolio_lib.reporter import write_ticker_report
    from portfolio_lib.digest import write_digest
    from portfolio_lib.slack_notifier import post_digest as post_slack_digest
    from portfolio_lib.prices import get_price
    from portfolio_lib.macro import fetch_macro_snapshot
    from portfolio_lib.signal_log import grade_open_signals, tag_compliance_block
    from portfolio_lib.memory_seed import build_doctrine_context
    from portfolio_lib.cost_tracker import CostTracker, append_cost_log
    from portfolio_lib.discovery import suggest_tickers
    from portfolio_lib.persona import render as render_persona
    from portfolio_lib.executor import stage_pending_order
    from portfolio_lib.loader import persist_watchlist_additions

    run_cfg = RunConfig.default(
        use_alpha_vantage=args.av,
        deep_mode=args.deep,
        analyst_preset=args.analyst_set,
        portfolio_path=Path(args.portfolio) if args.portfolio else None,
    )
    portfolio = load_portfolio(run_cfg.portfolio_path)

    if args.reset_alpaca:
        from portfolio_lib.alpaca_sync import reset_to_fidelity
        print(f"Resetting Alpaca paper account to Fidelity baseline…")
        if args.dry_run:
            print("[DRY RUN] Would cancel all Alpaca orders, close all positions, "
                  f"and write alpaca_portfolio.json from {run_cfg.portfolio_path.name}.")
        else:
            reset_to_fidelity(
                run_cfg.portfolio_path,
                run_cfg.alpaca_portfolio_path,
            )
            print(f"Done. alpaca_portfolio.json written to {run_cfg.alpaca_portfolio_path}")
        return

    if args.dry_run:
        print(f"Dry run OK. Portfolio loaded: {len(portfolio.holdings)} holdings, {len(portfolio.watch_list)} watchlist.")
        print(f"Analyst set: {run_cfg.analyst_preset} -> {run_cfg.selected_analysts}")
        print(f"Deep mode: {run_cfg.deep_mode} (debate rounds: {run_cfg.llm_config['max_debate_rounds']})")
        return

    _check_env()

    tickers_filter = set(t.upper() for t in args.tickers) if args.tickers else None

    # Phase 3: fetch macro snapshot once for the whole run
    logger.info("Fetching macro regime snapshot…")
    macro_snapshot = fetch_macro_snapshot()
    logger.info("Macro regime: %s", macro_snapshot.regime)

    # Pre-fetch prices for portfolio context (uses cache — no extra yfinance calls during analysis)
    all_tickers = [h.ticker for h in portfolio.holdings if h.entry and h.entry > 0]
    prices: dict = {t: get_price(t) for t in all_tickers}

    # Phase 4: grade any open signals that have aged past their lookback window
    graded = grade_open_signals(run_cfg.results_dir, get_price)
    if graded:
        logger.info("Graded %d signal(s) from previous runs", graded)

    cost_tracker = CostTracker()

    doctrine = build_doctrine_context(run_cfg.results_dir)
    if doctrine:
        run_cfg.llm_config["doctrine_context"] = doctrine

    ta = TradingAgentsGraph(
        selected_analysts=run_cfg.selected_analysts,
        debug=False,
        config=run_cfg.llm_config,
        callbacks=[cost_tracker],
    )

    results = []
    skipped: list[tuple[str, str]] = []
    all_candidates: list = []
    survivors: list = []
    near_misses: list = []

    # Determine which tickers to analyze from each pool.
    # When --watchlist + --tickers are both given, route each ticker to the
    # correct branch (holding vs watchlist) rather than filtering one pool only.
    _holding_ticker_set = {h.ticker for h in portfolio.holdings if h.entry > 0}
    _watchlist_ticker_set = {t for t, _ in iter_watchlist(portfolio)}

    if args.watchlist and tickers_filter:
        unknown = tickers_filter - _holding_ticker_set - _watchlist_ticker_set
        if unknown:
            logger.warning(
                "Ticker(s) not found in holdings or watchlist, skipping: %s",
                ", ".join(sorted(unknown)),
            )
        run_holdings = tickers_filter & _holding_ticker_set
        run_watchlist = tickers_filter & _watchlist_ticker_set
    elif args.watchlist:
        run_holdings = set()
        run_watchlist = _watchlist_ticker_set
    elif not args.discover_only:
        run_holdings = tickers_filter if tickers_filter is not None else _holding_ticker_set
        run_watchlist = set()
    else:
        run_holdings = set()
        run_watchlist = set()

    if run_holdings:
        for h in portfolio.holdings:
            if h.entry == 0.0:
                skipped.append((h.ticker, "warrant/special security — no entry price"))

        for holding in iter_holdings(portfolio):
            if holding.ticker not in run_holdings:
                continue

            if holding.role == "tax-loss-harvest":
                _print_tax_loss_status(holding, portfolio, prices)
                continue

            extra_ctx = _build_extra_context(portfolio, prices, holding.ticker, macro_snapshot)
            run_cfg.llm_config["extra_instrument_context"] = extra_ctx

            try:
                result = analyze_ticker(
                    ta=ta,
                    ticker=holding.ticker,
                    analysis_date=run_cfg.analysis_date,
                    kind=TickerKind.HOLDING,
                    entry=holding.entry,
                    shares=holding.shares,
                    target=None,
                    acquired_date=holding.acquired_date,
                    results_dir=run_cfg.results_dir,
                )
                report_path = write_ticker_report(result, run_cfg.results_dir, run_cfg.analysis_date, run_cfg.run_timestamp, deep_mode=run_cfg.deep_mode, analyst_preset=run_cfg.analyst_preset)
                blocked_rule = stage_pending_order(
                    ticker=result.ticker,
                    decision=result.decision,
                    kind=result.kind,
                    cash_balance=portfolio.cash_balance,
                    current_price=prices.get(result.ticker),
                    target_price=None,
                    report_path=report_path,
                    results_dir=run_cfg.results_dir,
                    portfolio=portfolio,
                    alpaca_portfolio_path=run_cfg.alpaca_portfolio_path,
                )
                if blocked_rule:
                    tag_compliance_block(run_cfg.results_dir, result.ticker, run_cfg.analysis_date, blocked_rule)
                results.append(result)
            except Exception as exc:
                logger.error("Failed to analyze %s: %s", holding.ticker, exc, exc_info=True)
                skipped.append((holding.ticker, f"ERROR: {exc}"))

    if run_watchlist:
        for ticker, target in iter_watchlist(portfolio):
            if ticker not in run_watchlist:
                continue

            extra_ctx = _build_extra_context(portfolio, prices, ticker, macro_snapshot)
            run_cfg.llm_config["extra_instrument_context"] = extra_ctx

            try:
                result = analyze_ticker(
                    ta=ta,
                    ticker=ticker,
                    analysis_date=run_cfg.analysis_date,
                    kind=TickerKind.WATCHLIST,
                    entry=None,
                    shares=0.0,
                    target=target,
                    results_dir=run_cfg.results_dir,
                )
                report_path = write_ticker_report(result, run_cfg.results_dir, run_cfg.analysis_date, run_cfg.run_timestamp, deep_mode=run_cfg.deep_mode, analyst_preset=run_cfg.analyst_preset)
                blocked_rule = stage_pending_order(
                    ticker=result.ticker,
                    decision=result.decision,
                    kind=result.kind,
                    cash_balance=portfolio.cash_balance,
                    current_price=get_price(result.ticker),
                    target_price=target,
                    report_path=report_path,
                    results_dir=run_cfg.results_dir,
                    portfolio=portfolio,
                    alpaca_portfolio_path=run_cfg.alpaca_portfolio_path,
                )
                if blocked_rule:
                    tag_compliance_block(run_cfg.results_dir, result.ticker, run_cfg.analysis_date, blocked_rule)
                results.append(result)
            except Exception as exc:
                logger.error("Failed to analyze %s: %s", ticker, exc, exc_info=True)
                skipped.append((ticker, f"ERROR: {exc}"))

    # ── Discovery pass (optional) ──────────────────────────────────────────────
    if args.discover and (not args.watchlist or args.discover_only):
        held_tickers      = [h.ticker for h in portfolio.holdings]
        watchlist_tickers = [t for t, _ in iter_watchlist(portfolio)]
        exclude_tickers   = list(dict.fromkeys(held_tickers + watchlist_tickers))
        print(f"\nDiscovering {args.discover} candidates "
              f"(layer gaps analysed · {macro_snapshot.regime} regime)...")
        all_candidates = suggest_tickers(
            persona_block=render_persona(),
            exclude=exclude_tickers,
            macro_snapshot=macro_snapshot,
            n=args.discover,
            llm_config=run_cfg.llm_config,
            portfolio=portfolio,
        )
        survivors   = [c for c in all_candidates if c.passed]
        near_misses = [c for c in all_candidates if c.near_miss and not c.passed]
        if survivors:
            print(f"\nAnalyzing {len(survivors)} survivor(s): "
                  f"{', '.join(c.ticker for c in survivors)}")
        for candidate in survivors:
            extra_ctx = _build_extra_context(portfolio, prices, candidate.ticker, macro_snapshot)
            run_cfg.llm_config["extra_instrument_context"] = extra_ctx
            try:
                result = analyze_ticker(
                    ta=ta,
                    ticker=candidate.ticker,
                    analysis_date=run_cfg.analysis_date,
                    kind=TickerKind.CANDIDATE,
                    entry=None,
                    shares=0.0,
                    target=None,
                    results_dir=None,   # skip signal log for candidates
                )
                write_ticker_report(result, run_cfg.results_dir, run_cfg.analysis_date, run_cfg.run_timestamp, deep_mode=run_cfg.deep_mode, analyst_preset=run_cfg.analyst_preset)
                results.append(result)
            except Exception as exc:
                logger.error("Failed to analyze candidate %s: %s", candidate.ticker, exc, exc_info=True)
                skipped.append((candidate.ticker, f"ERROR: {exc}"))

    # Persist discovery survivors + near-misses to portfolio.json watchlist.
    # Gate: skip any candidate whose yfinance metrics are entirely n/a — those
    # are likely delisted or mis-named tickers and have no business on the watchlist.
    def _has_valid_metrics(c) -> bool:
        return any(v is not None for v in c.metrics.values()) if c.metrics else False

    if args.discover and all_candidates:
        watchlist_additions = (
            [c.ticker for c in survivors
             if _has_valid_metrics(c) and not any(
                 c.ticker == h.ticker for h in portfolio.holdings
             )]
            + [c.ticker for c in near_misses if _has_valid_metrics(c)]
        )
        if watchlist_additions:
            added = persist_watchlist_additions(
                run_cfg.portfolio_path,
                watchlist_additions,
                targets=None,
            )
            if added:
                near_miss_tickers = {c.ticker for c in near_misses}
                for ticker in added:
                    label = "near-miss" if ticker in near_miss_tickers else "survivor"
                    print(f"  Watchlist +  {ticker} ({label})")

    if results:
        analyzed_tickers = [r.ticker for r in results]
        cost_record = cost_tracker.to_dict(run_cfg.run_timestamp, analyzed_tickers)
        append_cost_log(run_cfg.results_dir, cost_record)

        rejected_candidates = [c for c in all_candidates if not c.passed]

        digest_path = write_digest(
            results,
            run_cfg.results_dir,
            run_cfg.analysis_date,
            skipped,
            cash_balance=portfolio.cash_balance,
            run_timestamp=run_cfg.run_timestamp,
            run_cost_usd=cost_tracker.total_usd,
            rejected_candidates=rejected_candidates or None,
            regime=macro_snapshot.regime,
        )

        slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
        if slack_webhook:
            try:
                post_slack_digest(results, macro_snapshot.regime, cost_tracker.total_usd, portfolio.cash_balance, run_cfg.analysis_date, slack_webhook, run_timestamp=run_cfg.run_timestamp)
            except Exception as exc:
                logger.warning("Slack digest failed: %s", type(exc).__name__)

        bkd = cost_tracker.breakdown()
        cost_detail = "  ".join(
            f"{m}: {d['input_tokens']:,}in/{d['output_tokens']:,}out (${d['cost_usd']:.3f})"
            for m, d in bkd.items()
        )
        print(f"\nDone. Reports in: {run_cfg.results_dir}")
        print(f"Summary: {digest_path}")
        print(f"Macro regime: {macro_snapshot.regime}")
        print(f"Run cost: ${cost_tracker.total_usd:.4f}  [{cost_detail}]")
    else:
        print("\nNo tickers analyzed.")


if __name__ == "__main__":
    main()
