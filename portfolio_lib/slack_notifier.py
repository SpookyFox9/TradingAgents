import json
import logging
import re
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

_ACTIONABLE = {"BUY", "SELL", "OVERWEIGHT", "UNDERWEIGHT"}


def _fmt(p: Optional[float]) -> str:
    return f"${p:,.2f}" if p is not None else "n/a"


def _pnl_str(entry: float, current: Optional[float]) -> str:
    if current is None:
        return "n/a"
    pct = (current - entry) / entry * 100
    sign = "+" if pct >= 0 else ""
    return f"{_fmt(current)} ({sign}{pct:.1f}%)"


def _trader_verdict(text: str, max_chars: int = 140) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'FINAL TRANSACTION PROPOSAL[*:\s]+([^\n*]+)', text, re.IGNORECASE)
    verdict = m.group(1).strip().rstrip('*').strip() if m else None
    m2 = re.search(r'\*{0,2}Specific Action[*:\s]+([^\n]+)', text, re.IGNORECASE)
    if m2:
        action = m2.group(1).strip()
        end = action.find('. ')
        action = action[:end + 1] if end > 0 else action
        action = (action[:max_chars].rstrip() + '...') if len(action) > max_chars else action
        return f"{verdict} — {action}" if verdict else action
    return verdict


def _risk_rationale(text: str, max_chars: int = 160) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'\*{0,2}Action[*:\s]+([^\n|]+)', text, re.IGNORECASE)
    if m:
        rationale = m.group(1).strip().rstrip('*').strip()
        return (rationale[:max_chars].rstrip() + '...') if len(rationale) > max_chars else rationale
    for line in text.splitlines():
        line = re.sub(r'[*#|]', '', line).strip()
        if len(line) > 40:
            return (line[:max_chars].rstrip() + '...') if len(line) > max_chars else line
    return None


def _extract_stop(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'\*\*Hard Stop\*\*\s*\|?\s*([^\n|]+)', text)
    if not m:
        return None
    raw = m.group(1).strip()
    price_m = re.search(r'\$([\d,]+)', raw)
    return f"${price_m.group(1)}" if price_m else None


def _extract_trim(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'\*\*Partial Profit Target\*\*\s*\|?\s*([^\n|]+)', text)
    if not m:
        return None
    raw = m.group(1).strip()
    range_m = re.search(r'\$([\d,]+)[–—-]\$?([\d,]+)', raw)
    if range_m:
        return f"${range_m.group(1)}-${range_m.group(2)}"
    price_m = re.search(r'\$([\d,]+)', raw)
    return f"${price_m.group(1)}" if price_m else None


def _action_text(verdict: Optional[str]) -> Optional[str]:
    """Strip the decision label prefix from a trader verdict, leaving just the action sentence."""
    if not verdict:
        return None
    return verdict.split(' — ', 1)[-1] if ' — ' in verdict else verdict


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

    holdings = [r for r in results if r.kind == TickerKind.HOLDING]
    watchlist = [r for r in results if r.kind == TickerKind.WATCHLIST]
    candidates = [r for r in results if r.kind == TickerKind.CANDIDATE]
    prices = {r.ticker: get_price(r.ticker) for r in results}
    action_results = [r for r in results if r.decision.upper() in _ACTIONABLE]

    lines: list[str] = []

    # ── Action items at the top ──────────────────────────────────────────────
    if action_results:
        for r in action_results:
            emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
            act = _action_text(_trader_verdict(r.trader_investment_plan))
            top_line = f":zap: {emoji} *{r.ticker}: {r.decision}*"
            if act:
                top_line += f" — {act}"
            lines.append(top_line)
        lines.append("")

    # ── Header ───────────────────────────────────────────────────────────────
    lines += [f":bar_chart: *StockBoy {ts}* | `{regime or 'unknown'}` | {cost_str}", ""]

    # ── Holdings ─────────────────────────────────────────────────────────────
    if holdings:
        for r in holdings:
            emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
            current = prices[r.ticker]
            pnl = _pnl_str(r.entry, current)
            stop = _extract_stop(r.risk_judge_decision)
            trim = _extract_trim(r.risk_judge_decision)
            levels = " | ".join(filter(None, [
                f"Stop {stop}" if stop else None,
                f"Trim {trim}" if trim else None,
            ]))
            act = _action_text(_trader_verdict(r.trader_investment_plan))

            lines.append(f"{emoji} *{r.ticker}* — {r.decision}")
            lines.append(f"  {pnl} | {r.shares:.0f}sh @ {_fmt(r.entry)}")
            if levels:
                lines.append(f"  {levels}")
            if act:
                lines.append(f"  _{act}_")
        lines.append("")

    # ── Watchlist ────────────────────────────────────────────────────────────
    if watchlist:
        for r in watchlist:
            emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
            current = prices[r.ticker]
            dist = ""
            if current is not None and r.target is not None:
                pct = (r.target - current) / current * 100
                dist = f" | Target {_fmt(r.target)} ({pct:+.1f}%)"
            act = _action_text(_trader_verdict(r.trader_investment_plan))

            lines.append(f"{emoji} *{r.ticker}* — {r.decision} (Watchlist)")
            lines.append(f"  {_fmt(current)}{dist}")
            if act:
                lines.append(f"  _{act}_")
        lines.append("")

    # ── Discovery candidates ─────────────────────────────────────────────────
    if candidates:
        for r in candidates:
            emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
            act = _action_text(_trader_verdict(r.trader_investment_plan))

            lines.append(f"{emoji} *{r.ticker}* — {r.decision} (Discovery)")
            lines.append(f"  {_fmt(prices[r.ticker])}")
            if act:
                lines.append(f"  _{act}_")
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
