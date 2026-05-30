"""Build doctrine context string for injection into TradingAgentsGraph config."""
import logging
from pathlib import Path

from .signal_log import read_graded_signals

logger = logging.getLogger(__name__)

_DOCTRINE_SEEDS = [
    (
        "AI infrastructure stock with strong revenue growth, high gross margins, dominant market share, "
        "and rising capex from hyperscalers",
        "Barbell Engine position: hold through volatility if above 50-SMA. Trim to 3-4% if weight exceeds 5%. "
        "Require close above recent high with volume confirmation before adding.",
    ),
    (
        "Defensive utility or consumer-staples anchor with stable cash flows and dividend yield, "
        "held for portfolio balance during risk-off regimes",
        "Barbell Shield position: prioritize capital preservation over upside. "
        "Hold unless fundamental deterioration (rising debt, falling FCF). "
        "Expect lower growth — this is the defensive layer, not the engine.",
    ),
    (
        "Speculative position with large unrealized loss held for potential tax-loss harvest",
        "Treat as a strategic tax asset. Do not average down. "
        "Hold until December if no fundamental recovery thesis; "
        "use realized loss to offset capital gains elsewhere.",
    ),
    (
        "Stock with forward P/E significantly below TTM P/E where estimates have not been refreshed "
        "following a material negative event",
        "Do not trust forward P/E until sell-side revises estimates post-event. "
        "'Priced in' and 'fully understood' are different states. "
        "Wait for next earnings guidance before adding capital.",
    ),
    (
        "AI stack dependency analysis: power grid ETF or utility underperforming while AI chip demand accelerates",
        "Grid is the foundation of the AI stack: software requires chips, chips require cooling, "
        "cooling requires power. A grid capacity constraint is a leading indicator of AI infrastructure bottleneck. "
        "Monitor utility sector relative performance as a proxy for AI buildout sustainability.",
    ),
    (
        "Rising VIX above 25 combined with price below 200-day SMA and negative sector rotation",
        "Risk-Off regime confirmed. Execute trailing stops on winners immediately — "
        "treat breached stop like a security incident. Raise cash. "
        "Revisit entry after VIX settles below 20 and SPY reclaims 200-SMA.",
    ),
    (
        "Stock with high beta, strong recent momentum, but high-volume down day on consolidation",
        "High-volume distribution days during consolidation are institutional exits. "
        "Do not dismiss as healthy profit-taking. "
        "Require re-confirmation of trend above key level before adding.",
    ),
]


def build_doctrine_context(results_dir: Path) -> str:
    """Return a prose doctrine block for injection into config['doctrine_context'].

    Combines static GARP/barbell rules with graded past signals from signal_log.jsonl.
    The string is prepended to the TradingMemoryLog entries in _build_past_context().
    """
    parts: list[str] = []

    # Static doctrine
    doctrine_lines = ["## Portfolio Strategy Rules"]
    for situation, rule in _DOCTRINE_SEEDS:
        doctrine_lines.append(f"- **Situation**: {situation}\n  **Rule**: {rule}")
    parts.append("\n".join(doctrine_lines))

    # Graded past signals
    graded = read_graded_signals(results_dir, lookback_days=90)
    if graded:
        signal_lines = ["## Graded Past Signals (last 90 days)"]
        for row in graded:
            if row["realized_return_pct"] is None:
                continue
            price_str = f" @ ${row['price_at_decision']:.2f}" if row.get("price_at_decision") else ""
            outcome_str = f"{row['realized_return_pct']:+.1f}% — {row['grade']}"
            marker = "★ Confirmed:" if row["grade"] == "Correct" else "✗ Wrong:"
            signal_lines.append(
                f"- {row['date']} {row['ticker']}{price_str}: {row['decision']} → {marker} {outcome_str}"
            )
        if len(signal_lines) > 1:
            parts.append("\n".join(signal_lines))

    if not parts:
        return ""

    logger.debug("Built doctrine context: %d sections", len(parts))
    return "\n\n".join(parts)
