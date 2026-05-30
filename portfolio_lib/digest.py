import logging
from pathlib import Path
from typing import Optional

from .analyzer import TickerResult, TickerKind
from .notes import get_holding_notes, get_watchlist_notes
from .prices import get_price

logger = logging.getLogger(__name__)


def _fmt_price(p: Optional[float]) -> str:
    return f"${p:,.2f}" if p is not None else "n/a"


def _trailing_stop_value(entry: float, current: Optional[float]) -> Optional[float]:
    if current is not None and current > entry:
        return current * 0.95
    return None


def write_digest(results: list[TickerResult], results_dir: Path, analysis_date: str, skipped: list[tuple[str, str]], cash_balance: float = 0.0, run_timestamp: Optional[str] = None, run_cost_usd: Optional[float] = None, rejected_candidates: Optional[list] = None, regime: Optional[str] = None) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    filename_ts = run_timestamp or analysis_date
    out = results_dir / f"{filename_ts}_SUMMARY.md"

    holdings = [r for r in results if r.kind == TickerKind.HOLDING]
    watchlist = [r for r in results if r.kind == TickerKind.WATCHLIST]

    # Fetch all prices once to avoid repeated cache lookups and hidden coupling
    prices: dict[str, Optional[float]] = {r.ticker: get_price(r.ticker) for r in results}

    total_cost = sum((r.entry or 0) * (r.shares or 0) for r in holdings)

    total_value = total_cost + cash_balance
    lines: list[str] = [
        f"# Portfolio Summary — {analysis_date}",
        "",
        f"**Positions analyzed:** {len(holdings)} holdings, {len(watchlist)} watchlist  ",
        f"**Total cost basis:** ${total_cost:,.2f}  ",
        f"**Cash balance:** ${cash_balance:,.2f}  ",
        f"**Total account value (cost basis + cash):** ${total_value:,.2f}  ",
        f"**Market regime:** `{regime or 'unknown'}`",
        f"**Run cost:** ${run_cost_usd:.4f}" if run_cost_usd is not None else "**Run cost:** n/a",
        "",
        "---",
        "",
    ]

    # Decisions table
    lines += [
        "## Decisions",
        "",
        "| Ticker | Type | Decision | Entry / Target | Current | Note |",
        "|--------|------|----------|----------------|---------|------|",
    ]
    for r in results:
        current = prices[r.ticker]
        if r.kind == TickerKind.HOLDING:
            ref_price = _fmt_price(r.entry)
            notes = get_holding_notes(r.ticker)
        else:
            ref_price = f"T: {_fmt_price(r.target)}"
            notes = get_watchlist_notes(r.ticker, r.target)
        note_str = notes[0] if notes else ""
        lines.append(
            f"| {r.ticker} | {r.kind.value} | **{r.decision}** | {ref_price} | {_fmt_price(current)} | {note_str} |"
        )

    lines += ["", "---", ""]

    # Action items
    action_results = [r for r in results if r.decision.upper() in ("BUY", "SELL", "OVERWEIGHT", "UNDERWEIGHT")]
    if action_results:
        lines += ["## Action Items", ""]
        for r in action_results:
            if r.kind == TickerKind.HOLDING:
                notes = get_holding_notes(r.ticker)
            else:
                notes = get_watchlist_notes(r.ticker, r.target)
            is_intentional = any("INTENTIONAL" in n.upper() for n in notes)
            flag = " *(intentional hold — no action)*" if is_intentional else ""
            lines.append(f"- **{r.ticker}**: {r.decision}{flag}")
        lines += ["", "---", ""]

    # Trailing stops for winners
    winner_stops = []
    for r in holdings:
        if r.entry is None:
            continue
        current = prices[r.ticker]
        stop = _trailing_stop_value(r.entry, current)
        if stop is not None:
            pnl_pct = (current - r.entry) / r.entry * 100
            winner_stops.append((r.ticker, _fmt_price(stop), _fmt_price(current), f"+{pnl_pct:.1f}%"))

    if winner_stops:
        lines += ["## Trailing Stops (5% below current price — winners only)", ""]
        lines += ["| Ticker | Stop Level | Current | Gain |", "|--------|-----------|---------|------|"]
        for ticker, stop, curr, gain in winner_stops:
            lines.append(f"| {ticker} | {stop} | {curr} | {gain} |")
        lines += ["", "---", ""]

    # Watchlist targets
    if watchlist:
        lines += ["## Watchlist — Distance to Target", ""]
        for r in watchlist:
            current = prices[r.ticker]
            if current is not None and r.target is not None:
                dist = (r.target - current) / current * 100
                dist_str = f"{dist:+.1f}%"
            else:
                dist_str = "n/a"
            lines.append(f"- **{r.ticker}**: {r.decision} | current {_fmt_price(current)} | target {_fmt_price(r.target)} | distance {dist_str}")
        lines += ["", "---", ""]

    # Signal track record
    try:
        from .signal_log import render_track_record
        track_record = render_track_record(results_dir)
        lines += [track_record, "---", ""]
    except Exception as exc:
        logger.warning("Signal track record unavailable: %s", exc)

    # Discovery candidates (survivors — have full TickerResult in results)
    candidate_results = [r for r in results if r.kind == TickerKind.CANDIDATE]
    if candidate_results:
        lines += ["## Discovery — Candidates", ""]
        lines += [
            "| Ticker | Decision | PEG | ROE | FCF Margin | Verdict |",
            "|--------|----------|-----|-----|------------|---------|",
        ]
        for r in candidate_results:
            current = prices[r.ticker]
            lines.append(
                f"| {r.ticker} | **{r.decision}** | — | — | — | *see report* |"
            )
        lines += ["", "---", ""]

    # Discovery rejected — split into near-misses (watchlist added) and hard cuts
    if rejected_candidates:
        near_miss_list = [c for c in rejected_candidates if getattr(c, "near_miss", False)]
        cut_list       = [c for c in rejected_candidates if not getattr(c, "near_miss", False)]

        def _fmt_candidate_row(c) -> str:
            m = getattr(c, "metrics", {}) or {}
            peg = m.get("peg")
            roe = m.get("roe")
            fcf = m.get("fcf_margin")
            peg_str = f"{peg:.2f}" if peg is not None else "n/a"
            roe_str = f"{roe * 100:.1f}%" if roe is not None else "n/a"
            fcf_str = f"{fcf * 100:.1f}%" if fcf is not None else "n/a"
            return f"| {c.ticker} | {peg_str} | {roe_str} | {fcf_str} | {getattr(c, 'verdict', '')} |"

        if near_miss_list:
            lines += ["## Discovery — Near Misses (added to watchlist)", ""]
            lines += [
                "| Ticker | PEG | ROE | FCF Margin | Note |",
                "|--------|-----|-----|------------|------|",
            ]
            for c in near_miss_list:
                lines.append(_fmt_candidate_row(c))
            lines += ["", "---", ""]

        if cut_list:
            lines += ["## Discovery — Rejected by Screen", ""]
            lines += [
                "| Ticker | PEG | ROE | FCF Margin | Cut Reason |",
                "|--------|-----|-----|------------|------------|",
            ]
            for c in cut_list:
                lines.append(_fmt_candidate_row(c))
            lines += ["", "---", ""]

    # Skipped tickers
    if skipped:
        lines += ["## Skipped", ""]
        for ticker, reason in skipped:
            lines.append(f"- **{ticker}**: {reason}")
        lines += ["", "---", ""]

    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Saved digest: %s", out)
    print(f"\n  Digest -> {out.name}")
    return out
