"""Append-only signal log and outcome grader.

Every analyze_ticker() call records (date, ticker, decision, price).
After N days the grader backfills realized return and a Correct/Wrong/Neutral label.
"""
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GRADING_RULES = {
    # (decision_upper, lookback_days, correct_threshold_pct, wrong_threshold_pct)
    # Correct if realized return exceeds correct_threshold in the direction implied by decision
    # Wrong if return exceeds wrong_threshold in the opposite direction
    "BUY":         (14, +2.0, -2.0),
    "OVERWEIGHT":  (14, +2.0, -2.0),
    "HOLD":        (30,  5.0, -5.0),  # correct = |return| < 5%
    "UNDERWEIGHT": (14, -2.0, +2.0),
    "SELL":        (14, -2.0, +2.0),
}

_GRADE_VERSION = "v1"


def _log_path(results_dir: Path) -> Path:
    return results_dir / "signal_log.jsonl"


def record_decision(
    results_dir: Path,
    ticker: str,
    analysis_date: str,
    decision: str,
    price: Optional[float],
    *,
    compliance_block: Optional[str] = None,
) -> None:
    """Append one signal record. Idempotent — duplicate (date, ticker) is silently skipped."""
    log = _log_path(results_dir)
    record: dict = {
        "date": analysis_date,
        "ticker": ticker,
        "decision": decision.upper(),
        "price_at_decision": price,
        "realized_return_pct": None,
        "grade": None,
        "grade_date": None,
        "grade_version": _GRADE_VERSION,
    }
    if compliance_block:
        record["compliance_block"] = compliance_block

    existing_keys: set[str] = set()
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                existing_keys.add(f"{row['date']}|{row['ticker']}")
            except (json.JSONDecodeError, KeyError):
                continue

    key = f"{analysis_date}|{ticker}"
    if key in existing_keys:
        return

    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    logger.debug("Recorded signal: %s %s %s @ %s", analysis_date, ticker, decision, price)


def tag_compliance_block(
    results_dir: Path,
    ticker: str,
    analysis_date: str,
    rule_code: str,
) -> None:
    """Retroactively tag an existing signal record as compliance-blocked.

    Called when stage_pending_order blocks an order after the signal was already
    recorded by the analyzer. Adds compliance_block and sets grade='Blocked' so
    the signal is excluded from hit-rate statistics.
    """
    log = _log_path(results_dir)
    if not log.exists():
        return
    key = f"{analysis_date}|{ticker}"
    lines = log.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if f"{row.get('date')}|{row.get('ticker')}" == key and row.get("grade") is None:
                row["compliance_block"] = rule_code
                row["grade"] = "Blocked"
                row["grade_date"] = date.today().isoformat()
                line = json.dumps(row)
        except (json.JSONDecodeError, KeyError):
            pass
        updated.append(line)
    log.write_text("\n".join(updated) + "\n", encoding="utf-8")
    logger.debug("Tagged signal %s %s as compliance-blocked (%s)", analysis_date, ticker, rule_code)


def grade_open_signals(results_dir: Path, get_price_fn) -> int:
    """Backfill realized returns and grades for signals past their lookback window.

    Args:
        results_dir: Directory containing signal_log.jsonl
        get_price_fn: Callable(ticker) -> Optional[float]

    Returns:
        Number of signals graded this call.
    """
    log = _log_path(results_dir)
    if not log.exists():
        return 0

    today = date.today()
    lines = log.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    graded_count = 0

    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            updated.append(line)
            continue

        if row.get("grade") is not None:
            updated.append(line)
            continue

        # Skip signals that were never actionable due to a compliance block
        if row.get("compliance_block"):
            updated.append(line)
            continue

        decision = row.get("decision", "HOLD")
        rule = _GRADING_RULES.get(decision)
        if rule is None:
            updated.append(line)
            continue

        lookback, correct_thresh, wrong_thresh = rule
        signal_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        if (today - signal_date).days < lookback:
            updated.append(line)
            continue

        price_then = row.get("price_at_decision")
        if price_then is None:
            updated.append(line)
            continue

        price_now = get_price_fn(row["ticker"])
        if price_now is None:
            updated.append(line)
            continue

        realized_pct = (price_now - price_then) / price_then * 100

        if decision in ("BUY", "OVERWEIGHT"):
            grade = "Correct" if realized_pct >= correct_thresh else ("Wrong" if realized_pct <= wrong_thresh else "Neutral")
        elif decision in ("SELL", "UNDERWEIGHT"):
            grade = "Correct" if realized_pct <= correct_thresh else ("Wrong" if realized_pct >= wrong_thresh else "Neutral")
        else:  # HOLD
            grade = "Correct" if abs(realized_pct) <= abs(correct_thresh) else "Wrong"

        row["realized_return_pct"] = round(realized_pct, 2)
        row["grade"] = grade
        row["grade_date"] = today.isoformat()
        updated.append(json.dumps(row))
        graded_count += 1
        logger.info("Graded %s %s %s: %.1f%% → %s", row["date"], row["ticker"], decision, realized_pct, grade)

    log.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return graded_count


def read_graded_signals(results_dir: Path, lookback_days: int = 90) -> list[dict]:
    """Return all graded signals within lookback_days."""
    log = _log_path(results_dir)
    if not log.exists():
        return []
    cutoff = date.today() - timedelta(days=lookback_days)
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if row.get("grade") is None:
                continue
            if datetime.strptime(row["date"], "%Y-%m-%d").date() >= cutoff:
                rows.append(row)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return rows


def hit_rate_summary(results_dir: Path) -> dict[str, dict]:
    """Return {decision: {correct, wrong, neutral, total, hit_rate_pct}} for last 90 days."""
    rows = read_graded_signals(results_dir, lookback_days=90)
    stats: dict[str, dict] = {}
    for row in rows:
        d = row["decision"]
        if d not in stats:
            stats[d] = {"correct": 0, "wrong": 0, "neutral": 0, "total": 0}
        stats[d][row["grade"].lower()] += 1
        stats[d]["total"] += 1
    for d, s in stats.items():
        s["hit_rate_pct"] = round(s["correct"] / s["total"] * 100, 1) if s["total"] else 0.0
    return stats


def render_track_record(results_dir: Path) -> str:
    """Render a markdown Signal Track Record section for the digest."""
    stats = hit_rate_summary(results_dir)
    graded = read_graded_signals(results_dir, lookback_days=30)
    recent_misses = [r for r in graded if r["grade"] == "Wrong"][-5:]

    if not stats:
        return "## Signal Track Record\n\n*No graded signals yet — check back after signals age past their lookback window.*\n"

    lines = ["## Signal Track Record (last 90 days)", ""]
    lines += ["| Decision | Correct | Wrong | Neutral | Hit Rate |",
              "|----------|---------|-------|---------|---------|"]
    for decision, s in sorted(stats.items()):
        lines.append(
            f"| {decision} | {s['correct']} | {s['wrong']} | {s['neutral']} | {s['hit_rate_pct']}% |"
        )
    lines.append("")

    if recent_misses:
        lines.append("**Recent misses (last 30 days):**")
        for r in recent_misses:
            ret_str = f"{r['realized_return_pct']:+.1f}%" if r['realized_return_pct'] is not None else "n/a"
            lines.append(f"- {r['date']} {r['ticker']} {r['decision']} → {ret_str} in lookback")
        lines.append("")

    return "\n".join(lines)
