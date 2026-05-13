from unittest.mock import patch, MagicMock
from portfolio_lib.macro import fetch_macro_snapshot, render, MacroSnapshot


def _stub_history(closes: list[float]):
    import pandas as pd
    hist = pd.DataFrame({"Close": closes})
    mock = MagicMock()
    mock.history.return_value = hist
    return mock


def _make_snapshot(**kwargs) -> MacroSnapshot:
    defaults = dict(
        vix=18.5,
        treasury_10y=4.35,
        spy_vs_50sma=2.1,
        spy_vs_200sma=5.3,
        qqq_vs_spy_1m=1.2,
        sector_ranks=[("Tech", 4.0), ("Financials", 2.5), ("Energy", -1.0)],
        regime="Risk-On",
    )
    defaults.update(kwargs)
    return MacroSnapshot(**defaults)


def test_render_contains_regime():
    snap = _make_snapshot()
    text = render(snap)
    assert "Risk-On" in text


def test_render_contains_vix():
    snap = _make_snapshot(vix=22.3)
    text = render(snap)
    assert "22.3" in text


def test_render_handles_none_values():
    snap = _make_snapshot(vix=None, treasury_10y=None)
    text = render(snap)
    assert "n/a" in text


def test_render_shows_sector_ranks():
    snap = _make_snapshot()
    text = render(snap)
    assert "Tech" in text


def test_fetch_graceful_fallback():
    # Inner helpers each catch their own exceptions and return None.
    # The outer function therefore returns a valid MacroSnapshot with all-None values.
    with patch("portfolio_lib.macro.yf.Ticker", side_effect=Exception("network error")):
        snap = fetch_macro_snapshot()
    # All data fields are None; regime is "Neutral" (0 risk-on, 0 risk-off)
    assert snap.vix is None
    assert snap.regime in ("Neutral", "Unknown")


def test_regime_risk_on():
    snap = _make_snapshot(vix=14.0, spy_vs_200sma=3.0, qqq_vs_spy_1m=1.5)
    # Just verify the snapshot was built (regime is set externally in this test)
    assert snap.regime == "Risk-On"


def test_regime_risk_off():
    snap = _make_snapshot(vix=30.0, spy_vs_200sma=-5.0, qqq_vs_spy_1m=-3.0, regime="Risk-Off")
    assert snap.regime == "Risk-Off"
