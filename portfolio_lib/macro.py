"""Macro regime snapshot — one yfinance fetch per run, zero extra API quota."""
import logging
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

_SECTOR_ETFS = {
    "XLK": "Tech",
    "XLF": "Financials",
    "XLY": "Consumer Disc",
    "XLP": "Consumer Staples",
    "XLV": "Health Care",
    "XLE": "Energy",
    "XLU": "Utilities",
    "XLI": "Industrials",
    "XLC": "Comm Svcs",
}


@dataclass(frozen=True)
class MacroSnapshot:
    vix: Optional[float]
    treasury_10y: Optional[float]
    spy_vs_50sma: Optional[float]   # % above/below 50-day SMA
    spy_vs_200sma: Optional[float]
    qqq_vs_spy_1m: Optional[float]  # tech leadership ratio, 1-month change %
    sector_ranks: list[tuple[str, float]]  # (name, 1-month %) sorted best→worst
    regime: str  # "Risk-On", "Risk-Off", or "Neutral"


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b * 100


def _current_price(ticker: str) -> Optional[float]:
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def _sma(ticker: str, window: int) -> Optional[float]:
    try:
        hist = yf.Ticker(ticker).history(period=f"{window + 20}d")
        if len(hist) < window:
            return None
        return float(hist["Close"].tail(window).mean())
    except Exception:
        return None


def _one_month_return(ticker: str) -> Optional[float]:
    try:
        hist = yf.Ticker(ticker).history(period="35d")
        if len(hist) < 20:
            return None
        start = float(hist["Close"].iloc[-21])
        end = float(hist["Close"].iloc[-1])
        return (end - start) / start * 100 if start else None
    except Exception:
        return None


def _infer_regime(
    vix: Optional[float],
    spy_vs_200: Optional[float],
    qqq_spy: Optional[float],
) -> str:
    risk_on = 0
    risk_off = 0
    if vix is not None:
        if vix < 18:
            risk_on += 1
        elif vix > 25:
            risk_off += 1
    if spy_vs_200 is not None:
        if spy_vs_200 > 2:
            risk_on += 1
        elif spy_vs_200 < -2:
            risk_off += 1
    if qqq_spy is not None:
        if qqq_spy > 0:
            risk_on += 1
        elif qqq_spy < -2:
            risk_off += 1
    if risk_on > risk_off:
        return "Risk-On"
    if risk_off > risk_on:
        return "Risk-Off"
    return "Neutral"


def fetch_macro_snapshot() -> MacroSnapshot:
    """Pull macro signals via yfinance. Returns a snapshot with regime label."""
    logger.info("Fetching macro snapshot…")
    try:
        vix = _current_price("^VIX")
        tnx = _current_price("^TNX")  # 10Y yield (displayed as ×10 in yfinance)
        treasury_10y = tnx / 10 if tnx is not None else None  # normalize to actual %

        spy_price = _current_price("SPY")
        spy_50 = _sma("SPY", 50)
        spy_200 = _sma("SPY", 200)
        spy_vs_50 = _pct(spy_price, spy_50)
        spy_vs_200 = _pct(spy_price, spy_200)

        spy_1m = _one_month_return("SPY")
        qqq_1m = _one_month_return("QQQ")
        qqq_vs_spy = None
        if qqq_1m is not None and spy_1m is not None:
            qqq_vs_spy = qqq_1m - spy_1m

        sector_ranks: list[tuple[str, float]] = []
        for sym, name in _SECTOR_ETFS.items():
            ret = _one_month_return(sym)
            if ret is not None:
                sector_ranks.append((name, ret))
        sector_ranks.sort(key=lambda x: x[1], reverse=True)

        regime = _infer_regime(vix, spy_vs_200, qqq_vs_spy)

        return MacroSnapshot(
            vix=vix,
            treasury_10y=treasury_10y,
            spy_vs_50sma=spy_vs_50,
            spy_vs_200sma=spy_vs_200,
            qqq_vs_spy_1m=qqq_vs_spy,
            sector_ranks=sector_ranks,
            regime=regime,
        )
    except Exception as exc:
        logger.warning("Macro fetch failed (%s) — using empty snapshot", exc)
        return MacroSnapshot(
            vix=None, treasury_10y=None, spy_vs_50sma=None,
            spy_vs_200sma=None, qqq_vs_spy_1m=None,
            sector_ranks=[], regime="Unknown",
        )


def render(snapshot: MacroSnapshot) -> str:
    """Render the snapshot as a compact markdown block for agent injection."""
    def _fmt(v: Optional[float], suffix: str = "", decimals: int = 2) -> str:
        return f"{v:.{decimals}f}{suffix}" if v is not None else "n/a"

    top3 = snapshot.sector_ranks[:3]
    bot3 = snapshot.sector_ranks[-3:] if len(snapshot.sector_ranks) >= 3 else []

    lines = [
        "## MACRO REGIME SNAPSHOT",
        f"**Regime:** {snapshot.regime}",
        f"**VIX:** {_fmt(snapshot.vix, decimals=1)}  |  "
        f"**10Y Yield:** {_fmt(snapshot.treasury_10y, '%', 2)}",
        f"**SPY vs 50-SMA:** {_fmt(snapshot.spy_vs_50sma, '%', 1)}  |  "
        f"**SPY vs 200-SMA:** {_fmt(snapshot.spy_vs_200sma, '%', 1)}",
        f"**QQQ vs SPY (1M):** {_fmt(snapshot.qqq_vs_spy_1m, '%', 1)} "
        f"({'tech leading' if (snapshot.qqq_vs_spy_1m or 0) > 0 else 'tech lagging'})",
    ]
    if top3:
        lines.append(
            "**Top sectors (1M):** "
            + " | ".join(f"{n} {r:+.1f}%" for n, r in top3)
        )
    if bot3:
        lines.append(
            "**Weak sectors (1M):** "
            + " | ".join(f"{n} {r:+.1f}%" for n, r in bot3)
        )
    return "\n".join(lines)
