import json
import logging
import urllib.request
import urllib.error
from typing import Optional

from .analyzer import TickerResult, TickerKind
from .prices import get_price

logger = logging.getLogger(__name__)

_DECISION_EMOJI = {
    "BUY": ":large_green_circle:",
    "SELL": ":red_circle:",
    "OVERWEIGHT": ":large_green_circle:",
    "UNDERWEIGHT": ":large_yellow_circle:",
    "HOLD": ":white_circle:",
}


def _fmt(p: Optional[float]) -> str:
    return f"${p:,.2f}" if p is not None else "n/a"


def _pnl_str(entry: float, current: Optional[float], shares: float) -> str:
    if current is None:
        return "n/a"
    pct = (current - entry) / entry * 100
    sign = "+" if pct >= 0 else ""
    return f"{_fmt(current)} ({sign}{pct:.1f}%)"


def build_slack_text(
    results: list[TickerResult],
    regime: Optional[str],
    run_cost_usd: Optional[float],
    cash_balance: float,
    analysis_date: str,
    run_timestamp: Optional[str] = None,
) -> str:
    ts = run_timestamp or analysis_date
    cost_str = f"${run_cost_usd:.4f}" if run_cost_usd is not None else "n/a"
    header = f":bar_chart: *StockBoy — {ts}* | Regime: `{regime or 'unknown'}` | Cost: {cost_str}"

    holdings = [r for r in results if r.kind == TickerKind.HOLDING]
    watchlist = [r for r in results if r.kind == TickerKind.WATCHLIST]
    candidates = [r for r in results if r.kind == TickerKind.CANDIDATE]
    prices = {r.ticker: get_price(r.ticker) for r in results}

    lines = [header, ""]

    if holdings:
        lines.append("*Holdings*")
        for r in holdings:
            emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
            pnl = _pnl_str(r.entry, prices[r.ticker], r.shares)
            stop = _fmt(prices[r.ticker] * 0.95) if prices[r.ticker] and prices[r.ticker] > r.entry else "—"
            lines.append(f"  {emoji}  *{r.ticker}*  {r.decision}  {pnl}  Stop: {stop}")
        lines.append("")

    if watchlist:
        lines.append("*Watchlist*")
        for r in watchlist:
            emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
            current = prices[r.ticker]
            dist = ""
            if current is not None and r.target is not None:
                pct = (r.target - current) / current * 100
                dist = f"  -> target {_fmt(r.target)} ({pct:+.1f}%)"
            lines.append(f"  {emoji}  *{r.ticker}*  {r.decision}  {_fmt(current)}{dist}")
        lines.append("")

    if candidates:
        lines.append("*Discovery*")
        for r in candidates:
            emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
            lines.append(f"  {emoji}  *{r.ticker}*  {r.decision}  {_fmt(prices[r.ticker])}")
        lines.append("")

    action_results = [r for r in results if r.decision.upper() in ("BUY", "SELL", "OVERWEIGHT", "UNDERWEIGHT")]
    if action_results:
        lines.append(":zap: *Actions Required*")
        for r in action_results:
            lines.append(f"  • *{r.ticker}*: {r.decision}")
        lines.append("")

    lines.append(f"*Cash:* {_fmt(cash_balance)}")
    return "\n".join(lines)


def post_digest(
    results: list[TickerResult],
    regime: Optional[str],
    run_cost_usd: Optional[float],
    cash_balance: float,
    analysis_date: str,
    webhook_url: str,
    run_timestamp: Optional[str] = None,
) -> None:
    if not webhook_url.startswith("https://hooks.slack.com/"):
        logger.warning("SLACK_WEBHOOK_URL does not look like a Slack webhook — skipping")
        return

    text = build_slack_text(results, regime, run_cost_usd, cash_balance, analysis_date, run_timestamp)
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.warning("Slack webhook returned status %s", resp.status)
    except urllib.error.URLError as exc:
        logger.warning("Slack notification failed: %s", exc.reason)
    except Exception as exc:
        logger.warning("Slack notification failed: %s", type(exc).__name__)
