import logging
from pathlib import Path
from typing import Optional

from .analyzer import TickerResult, TickerKind
from .notes import get_holding_notes, get_watchlist_notes
from .prices import get_price

logger = logging.getLogger(__name__)

_SECTION_SEP = "\n\n---\n\n"


def _fmt_price(p: Optional[float]) -> str:
    return f"${p:,.2f}" if p is not None else "n/a"


def _trailing_stop(entry: float, current: Optional[float]) -> str:
    if current is None:
        return "n/a (price unavailable)"
    pnl_pct = (current - entry) / entry * 100
    if current > entry:
        stop = current * 0.95
        return f"{_fmt_price(stop)} (5% below current {_fmt_price(current)}, +{pnl_pct:.1f}%)"
    return f"N/A — position is underwater (current {_fmt_price(current)}, {pnl_pct:.1f}%)"


def write_ticker_report(result: TickerResult, results_dir: Path, analysis_date: str, run_timestamp: Optional[str] = None) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    filename_ts = run_timestamp or analysis_date
    out = results_dir / f"{filename_ts}_{result.ticker}.md"

    if result.kind == TickerKind.HOLDING:
        current = get_price(result.ticker)
        assert result.entry is not None  # iter_holdings guarantees this for HOLDING kind
        cost_basis = result.entry * result.shares
        pnl = ((current - result.entry) * result.shares) if current is not None else None
        pnl_str = f"${pnl:+,.2f}" if pnl is not None else "n/a"
        acquired = f" | **Acquired:** {result.acquired_date}" if result.acquired_date else ""
        header = (
            f"**Entry:** {_fmt_price(result.entry)} | "
            f"**Shares:** {result.shares} | "
            f"**Cost Basis:** ${cost_basis:,.2f} | "
            f"**Current:** {_fmt_price(current)} | "
            f"**Unrealized P&L:** {pnl_str}"
            f"{acquired}"
        )
        stop_line = f"**5% Trailing Stop:** {_trailing_stop(result.entry, current)}"
        notes = get_holding_notes(result.ticker)
    else:
        current = get_price(result.ticker)
        dist = None
        if current is not None and result.target is not None:
            dist = (result.target - current) / current * 100
        dist_str = f"{dist:+.1f}% to target" if dist is not None else "no target set"
        header = (
            f"**Type:** Watchlist | "
            f"**Current:** {_fmt_price(current)} | "
            f"**Target:** {_fmt_price(result.target)} | "
            f"**Distance:** {dist_str}"
        )
        stop_line = ""
        notes = get_watchlist_notes(result.ticker, result.target)

    notes_block = ""
    if notes:
        notes_block = "## Special Notes\n\n" + "\n".join(f"- {n}" for n in notes) + _SECTION_SEP

    sections = [
        f"# {result.ticker} Analysis — {analysis_date}",
        "",
        header,
        *(([stop_line, ""]) if stop_line else []),
        "",
        f"## Decision\n\n**{result.decision}**",
        _SECTION_SEP + notes_block if notes_block else _SECTION_SEP,
        f"## Investment Plan\n\n{result.investment_plan or '*Not available*'}",
        _SECTION_SEP,
        f"## Trader Plan\n\n{result.trader_investment_plan or '*Not available*'}",
        _SECTION_SEP,
        f"## Bull/Bear Judge Decision\n\n{result.invest_judge_decision or '*Not available*'}",
        _SECTION_SEP,
        f"## Risk Judge Decision\n\n{result.risk_judge_decision or '*Not available*'}",
        _SECTION_SEP,
        f"## Market Report\n\n{result.market_report or '*Not available*'}",
        _SECTION_SEP,
        f"## Fundamentals\n\n{result.fundamentals_report or '*Not available*'}",
        _SECTION_SEP,
        f"## News\n\n{result.news_report or '*Not available*'}",
        _SECTION_SEP,
        f"## Sentiment\n\n{result.sentiment_report or '*Not available*'}",
    ]

    content = "\n".join(sections)
    out.write_text(content, encoding="utf-8")
    logger.info("Saved report: %s", out)
    print(f"  Saved -> {out.name}")
    return out
