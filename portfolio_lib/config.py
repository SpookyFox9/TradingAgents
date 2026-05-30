import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from tradingagents.default_config import DEFAULT_CONFIG

_BASE = Path(__file__).resolve().parent.parent

# Analyst preset names → analyst list passed to TradingAgentsGraph
ANALYST_PRESETS: dict[str, list[str]] = {
    "full":    ["market", "social", "news", "fundamentals"],
    "quality": ["market", "news", "fundamentals"],   # drops social for long-term Buffett mode
    "fast":    ["market", "fundamentals"],
}


@dataclass(frozen=True)
class RunConfig:
    analysis_date: str       # YYYY-MM-DD  — used for propagate() and signal log
    run_timestamp: str       # YYYY-MM-DD_HHmm — used for output filenames only
    portfolio_path: Path
    alpaca_portfolio_path: Path  # tracks bot's paper-account positions
    results_dir: Path
    use_alpha_vantage: bool
    deep_mode: bool
    analyst_preset: str
    # Mutable dict — callers MUST copy via {**run_cfg.llm_config, ...} before per-call modification.
    llm_config: dict = field(compare=False)

    @property
    def selected_analysts(self) -> list[str]:
        return ANALYST_PRESETS.get(self.analyst_preset, ANALYST_PRESETS["full"])

    @classmethod
    def default(
        cls,
        use_alpha_vantage: bool = False,
        deep_mode: bool = False,
        analyst_preset: str = "quality",
        portfolio_path: Path | None = None,
    ) -> "RunConfig":
        debate_rounds = 2 if deep_mode else 1
        llm_config = {
            **DEFAULT_CONFIG,
            "llm_provider": "anthropic",
            "deep_think_llm": "claude-sonnet-4-6",
            "quick_think_llm": "claude-haiku-4-5",
            "anthropic_effort": None,  # effort param not supported on claude-sonnet-4-6
            "backend_url": None,
            "max_debate_rounds": debate_rounds,
            "max_risk_discuss_rounds": debate_rounds,
            "data_vendors": {
                "core_stock_apis": "yfinance",
                "technical_indicators": "alpha_vantage" if use_alpha_vantage else "yfinance",
                "fundamental_data": "yfinance",
                "news_data": "yfinance",
            },
            # Phase 1/3: filled in by analyze_portfolio.py after building context blocks
            "extra_instrument_context": "",
        }
        now = datetime.now()
        resolved_portfolio = portfolio_path or (_BASE / "portfolio.json")
        return cls(
            analysis_date=now.strftime("%Y-%m-%d"),
            run_timestamp=now.strftime("%Y-%m-%d_%H%M"),
            portfolio_path=resolved_portfolio,
            alpaca_portfolio_path=resolved_portfolio.parent / "alpaca_portfolio.json",
            results_dir=resolved_portfolio.parent / "Analysis",
            use_alpha_vantage=use_alpha_vantage,
            deep_mode=deep_mode,
            analyst_preset=analyst_preset,
            llm_config=llm_config,
        )
