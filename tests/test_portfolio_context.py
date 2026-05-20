from portfolio_lib.portfolio_context import build_context, _account_scale
from portfolio_lib.loader import Portfolio, Holding


def _make_portfolio(cash: float = 1_000.0) -> Portfolio:
    return Portfolio(
        holdings=[
            Holding(ticker="NVDA", entry=100.0, shares=2.0, acquired_date="2024-01-01"),
            Holding(ticker="PANW", entry=150.0, shares=2.0, acquired_date="2024-02-01"),
            Holding(ticker="GME",  entry=50.0,  shares=10.0, acquired_date=None),
            Holding(ticker="GMEWS", entry=0.0,   shares=4.0,  acquired_date=None),
        ],
        watch_list=["BRO"],
        targets={"BRO": 65.0},
        strategy="Buffett-style Quality & AI Infrastructure Growth",
        cash_balance=cash,
    )


def test_build_context_contains_active_ticker():
    portfolio = _make_portfolio()
    prices = {"NVDA": 110.0, "PANW": 165.0, "GME": 25.0}
    text = build_context(portfolio, prices, "NVDA")
    assert "NVDA" in text


def test_build_context_excludes_zero_entry():
    portfolio = _make_portfolio()
    prices = {"NVDA": 110.0, "PANW": 165.0}
    text = build_context(portfolio, prices, "NVDA")
    # GMEWS has entry=0 so is excluded from holdings table
    assert "GMEWS" not in text


def test_build_context_shows_cash():
    portfolio = _make_portfolio(cash=1_000.0)
    prices = {"NVDA": 110.0, "PANW": 165.0}
    text = build_context(portfolio, prices, "NVDA")
    assert "$1,000.00" in text


def test_build_context_shows_strategy():
    portfolio = _make_portfolio()
    prices = {}
    text = build_context(portfolio, prices, "PANW")
    assert "Buffett" in text


def test_build_context_watchlist_ticker():
    portfolio = _make_portfolio()
    prices = {}
    text = build_context(portfolio, prices, "BRO")
    assert "BRO" in text
    assert "watchlist" in text.lower()


def test_build_context_unrealized_pnl():
    portfolio = _make_portfolio()
    prices = {"NVDA": 110.0, "PANW": 165.0}
    text = build_context(portfolio, prices, "NVDA")
    # NVDA is +10% from 100.0 → 110.0
    assert "+" in text  # some positive P&L shown


# ── Account scale label ────────────────────────────────────────────────────────

def test_build_context_shows_account_scale_small():
    portfolio = _make_portfolio(cash=2_621.81)
    text = build_context(portfolio, {}, "NVDA")
    assert "Small account" in text


def test_build_context_shows_account_scale_large():
    portfolio = _make_portfolio(cash=250_000.0)
    text = build_context(portfolio, {}, "NVDA")
    assert "Large account" in text


# ── Small-account sizing note ──────────────────────────────────────────────────

def test_build_context_small_account_includes_sizing_note():
    portfolio = _make_portfolio(cash=5_000.0)
    text = build_context(portfolio, {}, "NVDA")
    assert "whole number of shares" in text


def test_build_context_large_account_omits_sizing_note():
    portfolio = _make_portfolio(cash=250_000.0)
    text = build_context(portfolio, {}, "NVDA")
    assert "whole number of shares" not in text


# ── _account_scale boundaries ─────────────────────────────────────────────────

def test_account_scale_below_small_threshold():
    assert _account_scale(9_999.99) == "Small account (<$10K)"


def test_account_scale_at_small_threshold():
    assert _account_scale(10_000.0) == "Mid-size account ($10K–$100K)"


def test_account_scale_at_mid_threshold():
    assert _account_scale(100_000.0) == "Large account (>$100K)"


def test_account_scale_large():
    assert _account_scale(500_000.0) == "Large account (>$100K)"
