from portfolio_lib.portfolio_context import build_context
from portfolio_lib.loader import Portfolio, Holding


def _make_portfolio() -> Portfolio:
    return Portfolio(
        holdings=[
            Holding(ticker="NVDA", entry=100.0, shares=1.0, acquired_date="2024-01-01"),
            Holding(ticker="PANW", entry=150.0, shares=2.0, acquired_date="2024-02-01"),
            Holding(ticker="GME",  entry=50.0,  shares=42.0, acquired_date=None),
            Holding(ticker="GMEWS", entry=0.0,   shares=4.0,  acquired_date=None),
        ],
        watch_list=["BRO"],
        targets={"BRO": 65.0},
        strategy="Buffett-style Quality & AI Infrastructure Growth",
        cash_balance=1000.0,
    )


def test_build_context_contains_active_ticker():
    portfolio = _make_portfolio()
    prices = {"NVDA": 198.79, "PANW": 181.16, "GME": 23.32}
    text = build_context(portfolio, prices, "NVDA")
    assert "NVDA" in text


def test_build_context_excludes_zero_entry():
    portfolio = _make_portfolio()
    prices = {"NVDA": 198.79, "PANW": 181.16}
    text = build_context(portfolio, prices, "NVDA")
    # GMEWS has entry=0 so is excluded from holdings table
    assert "GMEWS" not in text


def test_build_context_shows_cash():
    portfolio = _make_portfolio()
    prices = {"NVDA": 198.79, "PANW": 181.16}
    text = build_context(portfolio, prices, "NVDA")
    assert "701" in text  # cash balance


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
    prices = {"NVDA": 198.79, "PANW": 181.16}
    text = build_context(portfolio, prices, "NVDA")
    # NVDA is +10.6% from 100.0 → 198.79
    assert "+" in text  # some positive P&L shown
