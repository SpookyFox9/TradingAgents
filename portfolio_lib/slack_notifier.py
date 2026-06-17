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
_BLOCKED_EMOJI = ":no_entry:"


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


def _trader_stop(text: str) -> Optional[str]:
    """Extract Stop Loss value from trader plan structured field."""
    if not text:
        return None
    m = re.search(r'\*{0,2}Stop Loss\*{0,2}\s*[:\|]\s*([\d,\.]+)', text, re.IGNORECASE)
    if m:
        return f"${m.group(1)}"
    return _extract_stop(text)


def _trader_sizing(text: str, max_chars: int = 120) -> Optional[str]:
    """Extract first sentence of Position Sizing field from trader plan."""
    if not text:
        return None
    m = re.search(r'\*{0,2}Position Sizing\*{0,2}\s*[:\|]\s*([^\n]+)', text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).strip().rstrip('*').strip()
    end = raw.find('. ')
    sentence = raw[:end + 1] if end > 0 else raw
    return (sentence[:max_chars].rstrip() + '...') if len(sentence) > max_chars else sentence


def _trader_reasoning(text: str, max_chars: int = 160) -> Optional[str]:
    """Extract first sentence of Reasoning field from trader plan."""
    if not text:
        return None
    m = re.search(r'\*{0,2}Reasoning\*{0,2}\s*[:\|]\s*([^\n]+)', text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).strip().rstrip('*').strip()
    end = raw.find('. ')
    sentence = raw[:end + 1] if end > 0 else raw
    return (sentence[:max_chars].rstrip() + '...') if len(sentence) > max_chars else sentence


def _price_target(text: str) -> Optional[str]:
    """Extract Price Target from risk judge decision."""
    if not text:
        return None
    m = re.search(r'\*{0,2}Price Target\*{0,2}\s*[:\|]\s*([\d,\.]+)', text, re.IGNORECASE)
    return f"${m.group(1)}" if m else None


def _trader_entry_price(text: str) -> Optional[str]:
    """Extract Entry Price from trader plan structured field."""
    if not text:
        return None
    m = re.search(r'\*{0,2}Entry Price\*{0,2}\s*[:\|]\s*([\d,\.]+)', text, re.IGNORECASE)
    return f"${m.group(1)}" if m else None


def _trader_share_count(text: str) -> Optional[str]:
    """Extract integer share count from Position Sizing field, e.g. '3' from '3 shares ...'."""
    if not text:
        return None
    m = re.search(r'\*{0,2}Position Sizing\*{0,2}\s*[:\|]\s*(\d+)\s+share', text, re.IGNORECASE)
    return m.group(1) if m else None


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
    action_results = [r for r in results if r.decision.upper() in _ACTIONABLE and not r.blocked_rule]
    blocked_results = [r for r in results if r.decision.upper() in _ACTIONABLE and r.blocked_rule]

    lines: list[str] = []

    # ── Action items at the top ──────────────────────────────────────────────
    if action_results:
        for r in action_results:
            emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
            current = prices[r.ticker]
            if r.kind == TickerKind.WATCHLIST:
                shares = _trader_share_count(r.trader_investment_plan) or "?"
                stop = _trader_stop(r.trader_investment_plan)
                tx = f"*ACTION: BUY {shares}sh {r.ticker} @ ~{_fmt(current)}*"
                tx += f" | Stop {stop}" if stop else ""
                tx += f" | Target {_fmt(r.target)}" if r.target else ""
                lines.append(f":zap: {emoji} {tx}")
                reasoning = _trader_reasoning(r.trader_investment_plan)
                if reasoning:
                    lines.append(f"  _{reasoning}_")
            else:
                pnl = _pnl_str(r.entry, current) if r.entry else _fmt(current)
                lines.append(f":zap: {emoji} *{r.ticker}: {r.decision}* | {r.shares:.0f}sh @ {_fmt(r.entry)} → {pnl}")
                sizing = _trader_sizing(r.trader_investment_plan)
                reasoning = _trader_reasoning(r.trader_investment_plan)
                if sizing:
                    lines.append(f"  :pencil: {sizing}")
                if reasoning:
                    lines.append(f"  _{reasoning}_")
        lines.append("")

    # ── Compliance-blocked signals ───────────────────────────────────────────
    if blocked_results:
        for r in blocked_results:
            lines.append(f"{_BLOCKED_EMOJI} *{r.ticker}: {r.decision} — BLOCKED* | {r.blocked_rule}")
        lines.append("")

    # ── Header ───────────────────────────────────────────────────────────────
    lines += [f":bar_chart: *StockBoy {ts}* | `{regime or 'unknown'}` | {cost_str}", ""]

    # ── Holdings ─────────────────────────────────────────────────────────────
    if holdings:
        for r in holdings:
            if r.blocked_rule:
                emoji = _BLOCKED_EMOJI
                decision_label = f"{r.decision} *(blocked)*"
            else:
                emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
                decision_label = r.decision
            current = prices[r.ticker]
            pnl = _pnl_str(r.entry, current)
            stop = _trader_stop(r.trader_investment_plan) or _extract_stop(r.risk_judge_decision)
            trim = _extract_trim(r.risk_judge_decision)
            target = _price_target(r.risk_judge_decision)
            levels = " | ".join(filter(None, [
                f"Stop {stop}" if stop else None,
                f"Trim {trim}" if trim else None,
                f"Target {target}" if target else None,
            ]))
            reasoning = None if r.blocked_rule else _trader_reasoning(r.trader_investment_plan)
            sizing = None if r.blocked_rule else _trader_sizing(r.trader_investment_plan)

            lines.append(f"{emoji} *{r.ticker}* — {decision_label}")
            lines.append(f"  {pnl} | {r.shares:.0f}sh @ {_fmt(r.entry)}")
            if levels:
                lines.append(f"  {levels}")
            if sizing and r.decision.upper() in _ACTIONABLE:
                lines.append(f"  :pencil: {sizing}")
            if reasoning:
                lines.append(f"  _{reasoning}_")
        lines.append("")

    # ── Watchlist ────────────────────────────────────────────────────────────
    if watchlist:
        for r in watchlist:
            if r.blocked_rule:
                emoji = _BLOCKED_EMOJI
                decision_label = f"{r.decision} *(blocked)*"
            else:
                emoji = _DECISION_EMOJI.get(r.decision.upper(), ":white_circle:")
                decision_label = r.decision
            current = prices[r.ticker]
            dist = ""
            if current is not None and r.target is not None:
                pct = (r.target - current) / current * 100
                dist = f" | Target {_fmt(r.target)} ({pct:+.1f}%)"

            is_actionable = r.decision.upper() in _ACTIONABLE and not r.blocked_rule
            stop = _trader_stop(r.trader_investment_plan) if is_actionable else None
            shares = _trader_share_count(r.trader_investment_plan) if is_actionable else None
            reasoning = None if r.blocked_rule else _trader_reasoning(r.trader_investment_plan)

            lines.append(f"{emoji} *{r.ticker}* — {decision_label} (Watchlist)")
            if is_actionable:
                tx_parts = [f"BUY {shares}sh @ ~{_fmt(current)}" if shares else f"BUY @ ~{_fmt(current)}"]
                if stop:
                    tx_parts.append(f"Stop {stop}")
                if r.target and current:
                    pct = (r.target - current) / current * 100
                    tx_parts.append(f"Target {_fmt(r.target)} ({pct:+.1f}%)")
                lines.append(f"  {' | '.join(tx_parts)}")
            else:
                lines.append(f"  {_fmt(current)}{dist}")
            if reasoning:
                lines.append(f"  _{reasoning}_")
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
