"""Seed TradingAgentsGraph memory stores from graded signals and persona doctrine."""
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .signal_log import read_graded_signals

if TYPE_CHECKING:
    from tradingagents.graph.trading_graph import TradingAgentsGraph

logger = logging.getLogger(__name__)

_DOCTRINE_SEEDS: list[tuple[str, str]] = [
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


def seed_memories(ta: "TradingAgentsGraph", results_dir: Path) -> None:
    """Populate memory stores with persona doctrine + graded past signals."""
    logger.info("Seeding agent memories…")

    # 1. Seed doctrine into bull, bear, trader, and portfolio manager memories
    ta.bull_memory.add_situations(_DOCTRINE_SEEDS)
    ta.bear_memory.add_situations(_DOCTRINE_SEEDS)
    ta.trader_memory.add_situations(_DOCTRINE_SEEDS)
    ta.portfolio_manager_memory.add_situations(_DOCTRINE_SEEDS)
    logger.debug("Seeded %d doctrine entries into 4 memory stores", len(_DOCTRINE_SEEDS))

    # 2. Seed graded past signals (last 90 days) into memories
    graded = read_graded_signals(results_dir, lookback_days=90)
    if not graded:
        logger.debug("No graded signals yet — skipping historical seed")
        return

    signal_seeds: list[tuple[str, str]] = []
    for row in graded:
        if row["realized_return_pct"] is None:
            continue
        situation = (
            f"{row['ticker']} on {row['date']}: decision was {row['decision']}, "
            f"price was ${row['price_at_decision']:.2f}"
            if row.get("price_at_decision") else
            f"{row['ticker']} on {row['date']}: decision was {row['decision']}"
        )
        outcome = (
            f"Outcome after lookback: {row['realized_return_pct']:+.1f}% — grade: {row['grade']}."
        )
        if row["grade"] == "Wrong":
            outcome += " Review what was missed. Weight contrarian signals more heavily next time."
        elif row["grade"] == "Correct":
            outcome += " Analysis was on target. Similar thesis can be applied with confidence."
        signal_seeds.append((situation, outcome))

    if signal_seeds:
        # Weight correct signals 2× by duplicating them
        correct = [(s, r) for s, r in signal_seeds if "Correct" in r]
        weighted = signal_seeds + correct
        ta.bull_memory.add_situations(weighted)
        ta.bear_memory.add_situations(weighted)
        ta.invest_judge_memory.add_situations(weighted)
        logger.info("Seeded %d historical signal entries (%d weighted) into memories", len(signal_seeds), len(weighted))
