"""Stock discovery — two-pass GARP candidate screen.

Pass 1 (Haiku):   generate N+3 raw candidates with rationale, layer-gap aware
Data fetch:       yfinance GARP metrics per candidate (PEG, ROE, FCF margin, etc.)
Pass 2 (Sonnet):  challenge each with real metric table → PASS / CUT with reasoning
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import yfinance as yf
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


# ── AI infra layer definitions ─────────────────────────────────────────────────

AI_INFRA_LAYERS: dict[str, str] = {
    "grid":       "Power generation & grid infrastructure enabling AI data centers",
    "cooling":    "Thermal management & data center cooling systems",
    "chips":      "AI semiconductors & chip design",
    "software":   "AI software, security & platforms",
    "networking": "Data center networking & interconnect",
}

# Known tickers → their layer; extend as needed
TICKER_LAYERS: dict[str, str] = {
    "NVDA": "chips",   "AMD":  "chips",   "TSM":  "chips",   "MU":   "chips",
    "ARM":  "chips",   "INTC": "chips",   "QCOM": "chips",   "AVGO": "chips",
    "MRVL": "chips",
    "VRT":  "cooling", "SMCI": "cooling", "AAON": "cooling",
    "PANW": "software","CRWD": "software","ZS":   "software","MSFT": "software",
    "ORCL": "software","NOW":  "software","PLTR": "software","CRM":  "software",
    "ANET": "networking","CSCO": "networking","JNPR": "networking",
    "NEE":  "grid",    "CEG":  "grid",    "VST":  "grid",    "D":    "grid",
    "AES":  "grid",    "ETR":  "grid",    "DUK":  "grid",    "EXC":  "grid",
}


# ── Validation ────────────────────────────────────────────────────────────────

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
_MAX_RATIONALE_LEN = 500

# ── Data structures ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CandidateResult:
    ticker: str
    rationale: str   # pass 1 reasoning
    metrics: dict[str, Any]  # live yfinance data — may be empty on fetch failure
    verdict: str     # pass 2 verdict
    passed: bool


# ── Internal helpers ───────────────────────────────────────────────────────────

def _layer_coverage(holdings: list[str]) -> dict[str, list[str]]:
    coverage: dict[str, list[str]] = {layer: [] for layer in AI_INFRA_LAYERS}
    for ticker in holdings:
        layer = TICKER_LAYERS.get(ticker.upper())
        if layer:
            coverage[layer].append(ticker)
    return coverage


def _fetch_garp_metrics(ticker: str) -> dict[str, Any]:
    try:
        info = yf.Ticker(ticker).info
        revenue = info.get("totalRevenue") or 0
        fcf = info.get("freeCashflow")
        fcf_margin = (fcf / revenue) if (fcf and revenue) else None
        return {
            "peg":        info.get("pegRatio"),
            "roe":        info.get("returnOnEquity"),
            "fcf_margin": round(fcf_margin, 4) if fcf_margin is not None else None,
            "op_margin":  info.get("operatingMargins"),
            "rev_growth": info.get("revenueGrowth"),
        }
    except Exception as exc:
        logger.warning("Metrics fetch failed for %s: %s", ticker, exc)
        return {}


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v * 100:.1f}%" if v is not None else "n/a"


def _fmt_ratio(v: Optional[float]) -> str:
    return f"{v:.2f}" if v is not None else "n/a"


def _metrics_table(candidates: list[dict]) -> str:
    rows = [
        "| Ticker | PEG | ROE | FCF Margin | Op Margin | Rev Growth |",
        "|--------|-----|-----|------------|-----------|------------|",
    ]
    for c in candidates:
        m = c.get("metrics", {})
        rows.append(
            f"| {c['ticker']:<6} "
            f"| {_fmt_ratio(m.get('peg')):<4} "
            f"| {_fmt_pct(m.get('roe')):<7} "
            f"| {_fmt_pct(m.get('fcf_margin')):<10} "
            f"| {_fmt_pct(m.get('op_margin')):<9} "
            f"| {_fmt_pct(m.get('rev_growth')):<10} |"
        )
    return "\n".join(rows)


def _parse_json_list(text: str, key_check: str) -> list[dict]:
    """Extract a JSON array from LLM output, tolerating markdown fences."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict) and key_check in item]
    except json.JSONDecodeError:
        pass
    return []


def _build_llm(model: str, callbacks: list[Any]) -> ChatAnthropic:
    kwargs: dict[str, Any] = {"model": model}
    if callbacks:
        kwargs["callbacks"] = callbacks
    return ChatAnthropic(**kwargs)


# ── Public entry point ─────────────────────────────────────────────────────────

def suggest_tickers(
    persona_block: str,
    exclude: list[str],
    macro_snapshot: Any,
    n: int,
    llm_config: dict,
    portfolio=None,  # portfolio_lib.loader.Portfolio — used for compliance filter
) -> list[CandidateResult]:
    """Two-pass GARP discovery.

    Returns all CandidateResult objects sorted survivors-first.
    Callers check .passed to separate survivors from cuts.
    ``exclude`` should contain all held + watchlist tickers so discovery
    only surfaces genuinely new names.
    """
    from portfolio_lib.macro import render as render_macro

    callbacks    = llm_config.get("callbacks", [])
    haiku_model  = llm_config.get("quick_think_llm", "claude-haiku-4-5")
    sonnet_model = llm_config.get("deep_think_llm", "claude-sonnet-4-6")

    llm_quick = _build_llm(haiku_model, callbacks)
    llm_deep  = _build_llm(sonnet_model, callbacks)

    macro_block  = render_macro(macro_snapshot)
    coverage     = _layer_coverage(exclude)
    n_generate   = n + 3

    # ── Pass 1: Generate ───────────────────────────────────────────────────────

    gap_lines = [
        "  - {}: {}".format(
            layer.upper(),
            ", ".join(coverage[layer]) + " (covered)" if coverage[layer] else "EMPTY ← prioritize",
        )
        for layer in AI_INFRA_LAYERS
    ]

    gen_prompt = (
        f"{persona_block}\n\n"
        f"{macro_block}\n\n"
        "## DISCOVERY TASK — Pass 1: Generate Candidates\n\n"
        f"Already tracked (holdings + watchlist — exclude these): {', '.join(exclude) or 'none'}\n\n"
        "AI Infrastructure layer coverage:\n"
        + "\n".join(gap_lines)
        + f"\n\nGenerate {n_generate} distinct stock candidates."
        " Prioritize EMPTY layers before adding to covered ones.\n\n"
        "Each candidate must:\n"
        "- Meet GARP criteria: PEG < 1.5, ROIC > 15%, positive FCF margin\n"
        "- Fit the AI infra dependency stack (grid → cooling → chips → software → networking)\n"
        "- Have a specific moat argument — not generic 'AI tailwind' reasoning\n\n"
        "Return ONLY valid JSON, no other text:\n"
        '[\n  {"ticker": "XXX", "rationale": "one sentence citing PEG estimate, ROIC, and specific moat"},\n  ...\n]'
    )

    logger.info("Discovery pass 1 [%s]: generating %d raw candidates", haiku_model, n_generate)
    gen_response    = llm_quick.invoke([HumanMessage(content=gen_prompt)])
    raw_candidates  = _parse_json_list(gen_response.content, "ticker")

    if not raw_candidates:
        logger.warning("Discovery pass 1 returned no parseable candidates — aborting")
        return []

    print(f"  Pass 1 [Haiku]   -> {len(raw_candidates)} raw candidates: "
          f"{', '.join(c['ticker'].upper() for c in raw_candidates)}")

    # ── Data fetch ─────────────────────────────────────────────────────────────

    from portfolio_lib.compliance import is_blocked_ticker

    enriched: list[dict] = []
    for c in raw_candidates:
        ticker = c["ticker"].strip().upper()
        if not _TICKER_RE.match(ticker):
            logger.warning("Discovery pass 1 rejected malformed ticker: %r", ticker)
            continue
        if is_blocked_ticker(ticker, portfolio):
            logger.info("Discovery: %s filtered by compliance (warrant/lockout/harvest)", ticker)
            continue
        rationale = str(c.get("rationale", "")).strip()[:_MAX_RATIONALE_LEN]
        metrics = _fetch_garp_metrics(ticker)
        enriched.append({"ticker": ticker, "rationale": rationale, "metrics": metrics})

    if not enriched:
        logger.warning("Discovery pass 1 produced no valid tickers after sanitization — aborting")
        return []

    print(f"  Metrics fetched  -> {', '.join(c['ticker'] for c in enriched)}")

    # ── Pass 2: Challenge ──────────────────────────────────────────────────────

    covered_summary = ", ".join(
        f"{layer}: {', '.join(tickers)}"
        for layer, tickers in coverage.items() if tickers
    ) or "none"

    challenge_prompt = (
        f"{persona_block}\n\n"
        f"{macro_block}\n\n"
        "## DISCOVERY TASK — Pass 2: Challenge Candidates\n\n"
        f"Already tracked (holdings + watchlist — exclude these): {', '.join(exclude) or 'none'}\n"
        f"Covered layers: {covered_summary}\n\n"
        "Proposed candidates and rationale:\n"
        + json.dumps(
            [{"ticker": c["ticker"], "rationale": c["rationale"]} for c in enriched],
            indent=2,
        )
        + "\n\nLive GARP metrics from yfinance (treat n/a as a NEGATIVE signal):\n"
        + _metrics_table(enriched)
        + "\n\nYou are a skeptical GARP analyst. Challenge each candidate on:\n"
        "1. METRICS: Does real data confirm PEG < 1.5, ROE > 15%, positive FCF margin?\n"
        "2. NARRATIVE: Does the rationale cite specific facts or just 'AI tailwind' story?\n"
        "3. OVERLAP: Does it duplicate a layer already covered by current holdings?\n"
        "4. MOAT: Is the competitive advantage specific and durable, not commodity?\n\n"
        "Rules:\n"
        "- Any metric showing n/a is a negative signal\n"
        f"- Cap total PASS count at {n}\n"
        "- Be willing to cut the majority — quality over quantity\n\n"
        "Return ONLY valid JSON, no other text:\n"
        '[\n  {"ticker": "XXX", "verdict": "one sentence with specific reason", "passed": true},\n  ...\n]'
    )

    logger.info("Discovery pass 2 [%s]: challenging %d candidates", sonnet_model, len(enriched))
    challenge_response = llm_deep.invoke([HumanMessage(content=challenge_prompt)])
    verdicts           = _parse_json_list(challenge_response.content, "ticker")
    verdict_map: dict[str, dict] = {v["ticker"].upper(): v for v in verdicts}

    # ── Merge ──────────────────────────────────────────────────────────────────

    results: list[CandidateResult] = []
    for c in enriched:
        ticker = c["ticker"]
        v      = verdict_map.get(ticker, {})
        passed = bool(v.get("passed", False))
        results.append(CandidateResult(
            ticker=ticker,
            rationale=c["rationale"],
            metrics=c["metrics"],
            verdict=v.get("verdict", "No verdict returned by challenge pass"),
            passed=passed,
        ))

    results.sort(key=lambda r: (not r.passed, r.ticker))

    survivors = [r for r in results if r.passed]
    cuts      = [r for r in results if not r.passed]

    # Guarantee at least one survivor so the run is never empty
    if not survivors and results:
        logger.warning("All candidates cut — forcing top candidate through")
        top      = results[0]
        forced   = CandidateResult(
            ticker=top.ticker, rationale=top.rationale, metrics=top.metrics,
            verdict=top.verdict + " [forced — all others cut]", passed=True,
        )
        results  = [forced] + results[1:]
        survivors, cuts = [forced], results[1:]

    print(f"  Pass 2 [Sonnet]  -> {len(survivors)} PASS, {len(cuts)} CUT")
    for r in survivors:
        m = r.metrics
        tag = (f"PEG {_fmt_ratio(m.get('peg'))} | "
               f"ROE {_fmt_pct(m.get('roe'))} | "
               f"FCF {_fmt_pct(m.get('fcf_margin'))}")
        print(f"    PASS  {r.ticker:<6} {tag} — {r.verdict}")
    for r in cuts:
        print(f"    CUT   {r.ticker:<6} — {r.verdict}")

    return results
