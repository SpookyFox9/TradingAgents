import json
import logging
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD) — update when Anthropic revises rates
MODEL_RATES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80,  "output": 4.00},
    "claude-opus-4-7":            {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5":           {"input": 0.80,  "output": 4.00},
}
_DEFAULT_RATES: dict[str, float] = {"input": 3.00, "output": 15.00}  # fallback to Sonnet pricing


def _rates(model: str) -> dict[str, float]:
    for key in MODEL_RATES:
        if model.startswith(key) or key in model:
            return MODEL_RATES[key]
    logger.debug("Unknown model %r — applying Sonnet fallback pricing", model)
    return _DEFAULT_RATES


def _parse_usage(response: LLMResult) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from an LLMResult, trying multiple locations."""
    # Path 1: llm_output["usage"] — langchain_anthropic >= 0.3
    usage = (response.llm_output or {}).get("usage", {})
    if usage:
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    # Path 2: generation_info on the first generation
    try:
        gen_info = response.generations[0][0].generation_info or {}
        meta = gen_info.get("usage_metadata") or gen_info.get("usage") or {}
        if meta:
            return int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0))
    except (IndexError, AttributeError):
        pass

    return 0, 0


class CostTracker(BaseCallbackHandler):
    """LangChain callback that accumulates Anthropic token usage across a run."""

    def __init__(self) -> None:
        super().__init__()
        self._pending: dict[UUID, str] = {}   # run_id → model name
        self._records: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        model = (
            serialized.get("kwargs", {}).get("model")
            or serialized.get("name", "unknown")
        )
        self._pending[run_id] = str(model)

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        model = self._pending.pop(run_id, "unknown")
        input_tok, output_tok = _parse_usage(response)
        if input_tok == 0 and output_tok == 0:
            logger.debug("CostTracker: no usage data found for run_id %s model %s", run_id, model)
        self._records.append({"model": model, "input_tokens": input_tok, "output_tokens": output_tok})

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @property
    def total_usd(self) -> float:
        total = 0.0
        for rec in self._records:
            rates = _rates(rec["model"])
            total += rec["input_tokens"] / 1_000_000 * rates["input"]
            total += rec["output_tokens"] / 1_000_000 * rates["output"]
        return total

    def breakdown(self) -> dict[str, dict[str, Any]]:
        agg: dict[str, dict[str, Any]] = {}
        for rec in self._records:
            m = rec["model"]
            if m not in agg:
                agg[m] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            rates = _rates(m)
            agg[m]["input_tokens"] += rec["input_tokens"]
            agg[m]["output_tokens"] += rec["output_tokens"]
            agg[m]["cost_usd"] += (
                rec["input_tokens"] / 1_000_000 * rates["input"]
                + rec["output_tokens"] / 1_000_000 * rates["output"]
            )
        return agg

    def reset(self) -> None:
        self._pending.clear()
        self._records.clear()

    def to_dict(self, run_timestamp: str, tickers: list[str]) -> dict[str, Any]:
        return {
            "run_timestamp": run_timestamp,
            "tickers": tickers,
            "total_usd": round(self.total_usd, 4),
            "calls": len(self._records),
            "breakdown": {
                model: {**data, "cost_usd": round(data["cost_usd"], 4)}
                for model, data in self.breakdown().items()
            },
        }


def append_cost_log(results_dir: Path, record: dict[str, Any]) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "cost_log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    logger.debug("Cost record appended to %s", log_path)
