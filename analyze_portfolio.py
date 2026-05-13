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
            "  1. Copy TradingAgents\\.env.example to TradingAgents\\.env\n"
            "  2. Add your Anthropic API key\n"
            "  See SETUP.md for full instructions.\n"
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
    args = parser.parse_args()

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
    from portfolio_lib.prices import get_price
    from portfolio_lib.macro import fetch_macro_snapshot
    from portfolio_lib.signal_log import grade_open_signals
    from portfolio_lib.memory_seed import seed_memories
    from portfolio_lib.cost_tracker import CostTracker, append_cost_log
    from portfolio_lib.discovery import suggest_tickers
    from portfolio_lib.persona import render as render_persona

    run_cfg = RunConfig.default(
        use_alpha_vantage=args.av,
        deep_mode=args.deep,
        analyst_preset=args.analyst_set,
    )
    portfolio = load_portfolio(run_cfg.portfolio_path)

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
    run_cfg.llm_config["callbacks"] = [cost_tracker]

    ta = TradingAgentsGraph(
        selected_analysts=run_cfg.selected_analysts,
        debug=False,
        config=run_cfg.llm_config,
    )

    # Phase 5: seed memories from doctrine + past graded signals
    seed_memories(ta, run_cfg.results_dir)

    results = []
    skipped: list[tuple[str, str]] = []

    if not args.watchlist:
        for h in portfolio.holdings:
            if h.entry == 0.0:
                skipped.append((h.ticker, "warrant/special security — no entry price"))

        for holding in iter_holdings(portfolio):
            ticker = holding.ticker
            if tickers_filter and ticker not in tickers_filter:
                continue

            # Phase 1: build per-ticker extra context and inject into config
            extra_ctx = _build_extra_context(portfolio, prices, ticker, macro_snapshot)
            run_cfg.llm_config["extra_instrument_context"] = extra_ctx

            try:
                result = analyze_ticker(
                    ta=ta,
                    ticker=ticker,
                    analysis_date=run_cfg.analysis_date,
                    kind=TickerKind.HOLDING,
                    entry=holding.entry,
                    shares=holding.shares,
                    target=None,
                    acquired_date=holding.acquired_date,
                    results_dir=run_cfg.results_dir,
                )
                write_ticker_report(result, run_cfg.results_dir, run_cfg.analysis_date, run_cfg.run_timestamp)
                results.append(result)
            except Exception as exc:
                logger.error("Failed to analyze %s: %s", ticker, exc, exc_info=True)
                skipped.append((ticker, f"ERROR: {exc}"))

    else:
        for ticker, target in iter_watchlist(portfolio):
            if tickers_filter and ticker not in tickers_filter:
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
                write_ticker_report(result, run_cfg.results_dir, run_cfg.analysis_date, run_cfg.run_timestamp)
                results.append(result)
            except Exception as exc:
                logger.error("Failed to analyze %s: %s", ticker, exc, exc_info=True)
                skipped.append((ticker, f"ERROR: {exc}"))

    # ── Discovery pass (optional) ──────────────────────────────────────────────
    all_candidates = []
    if args.discover and not args.watchlist:
        held_tickers = [h.ticker for h in portfolio.holdings]
        print(f"\nDiscovering {args.discover} candidates "
              f"(layer gaps analysed · {macro_snapshot.regime} regime)...")
        all_candidates = suggest_tickers(
            persona_block=render_persona(),
            holdings=held_tickers,
            macro_snapshot=macro_snapshot,
            n=args.discover,
            llm_config=run_cfg.llm_config,
        )
        survivors = [c for c in all_candidates if c.passed]
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
                write_ticker_report(result, run_cfg.results_dir, run_cfg.analysis_date, run_cfg.run_timestamp)
                results.append(result)
            except Exception as exc:
                logger.error("Failed to analyze candidate %s: %s", candidate.ticker, exc, exc_info=True)
                skipped.append((candidate.ticker, f"ERROR: {exc}"))

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
        )

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
