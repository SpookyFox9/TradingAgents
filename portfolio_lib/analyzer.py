import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TickerKind(str, Enum):
    HOLDING = "HOLDING"
    WATCHLIST = "WATCHLIST"
    CANDIDATE = "CANDIDATE"


@dataclass(frozen=True)
class TickerResult:
    ticker: str
    kind: TickerKind
    entry: Optional[float]
    shares: float
    target: Optional[float]
    acquired_date: Optional[str]
    decision: str
    market_report: str
    sentiment_report: str
    news_report: str
    fundamentals_report: str
    investment_plan: str
    trader_investment_plan: str
    invest_judge_decision: str
    risk_judge_decision: str
    final_trade_decision: str
    blocked_rule: Optional[str] = None  # set post-compliance; e.g. "R7: breakout not confirmed..."


def _safe_str(state: dict[str, Any], key: str) -> str:
    val = state.get(key)
    if val is None:
        return ""
    return str(val)


def _safe_judge(debate_state: Any) -> str:
    if debate_state is None:
        return ""
    if isinstance(debate_state, dict):
        return str(debate_state.get("judge_decision", ""))
    return str(getattr(debate_state, "judge_decision", ""))


def analyze_ticker(
    ta: Any,
    ticker: str,
    analysis_date: str,
    kind: TickerKind,
    entry: Optional[float],
    shares: float,
    target: Optional[float],
    acquired_date: Optional[str] = None,
    results_dir: Optional[Path] = None,
) -> TickerResult:
    logger.info("Analyzing %s [%s]", ticker, kind.value)
    print(f"\n{'='*60}")
    label = f"Entry: ${entry:.2f} | Shares: {shares}" if entry else f"Target: ${target:.2f}" if target else "Watchlist"
    print(f"Analyzing {ticker} ({kind.value}) | {label}")
    print("=" * 60)

    final_state, decision = ta.propagate(ticker, analysis_date)

    if not isinstance(final_state, dict):
        raise TypeError(f"propagate() returned unexpected type {type(final_state)!r} for {ticker}")

    result = TickerResult(
        ticker=ticker,
        kind=kind,
        entry=entry,
        shares=shares,
        target=target,
        acquired_date=acquired_date,
        decision=decision,
        market_report=_safe_str(final_state, "market_report"),
        sentiment_report=_safe_str(final_state, "sentiment_report"),
        news_report=_safe_str(final_state, "news_report"),
        fundamentals_report=_safe_str(final_state, "fundamentals_report"),
        investment_plan=_safe_str(final_state, "investment_plan"),
        trader_investment_plan=_safe_str(final_state, "trader_investment_plan"),
        invest_judge_decision=_safe_judge(final_state.get("investment_debate_state")),
        risk_judge_decision=_safe_judge(final_state.get("risk_debate_state")),
        final_trade_decision=_safe_str(final_state, "final_trade_decision"),
    )

    if results_dir is not None:
        _record_signal(result, analysis_date, results_dir)

    return result


def _record_signal(result: TickerResult, analysis_date: str, results_dir: Path) -> None:
    try:
        from .signal_log import record_decision
        from .prices import get_price
        price = get_price(result.ticker)
        record_decision(results_dir, result.ticker, analysis_date, result.decision, price)
    except Exception as exc:
        logger.warning("Signal log record failed for %s: %s", result.ticker, exc)
