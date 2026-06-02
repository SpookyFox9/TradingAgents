import json
import pytest
from pathlib import Path
from portfolio_lib.loader import load_portfolio, iter_holdings, iter_watchlist


@pytest.fixture
def valid_portfolio_file(tmp_path: Path) -> Path:
    data = {
        "holdings": [
            {"ticker": "NVDA", "entry": 100.0, "shares": 2.0},
            {"ticker": "GME", "entry": 50.0, "shares": 10.0},
            {"ticker": "GMEWS", "entry": 0.0, "shares": 4.0},
        ],
        "watch_list": ["BRO", "BAC"],
        "targets": {"BRO": 65.0},
        "strategy": "Buffett",
    }
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_portfolio_parses_holdings(valid_portfolio_file):
    p = load_portfolio(valid_portfolio_file)
    assert len(p.holdings) == 3
    nvda = next(h for h in p.holdings if h.ticker == "NVDA")
    assert nvda.entry == pytest.approx(100.0)
    assert nvda.shares == 2.0


def test_load_portfolio_parses_watchlist(valid_portfolio_file):
    p = load_portfolio(valid_portfolio_file)
    assert "BRO" in p.watch_list
    assert p.targets["BRO"] == pytest.approx(65.0)


def test_iter_holdings_skips_zero_entry(valid_portfolio_file):
    p = load_portfolio(valid_portfolio_file)
    tickers = [h.ticker for h in iter_holdings(p)]
    assert "GMEWS" not in tickers
    assert "NVDA" in tickers
    assert "GME" in tickers


def test_load_portfolio_missing_key_raises(tmp_path):
    data = {"holdings": [{"ticker": "NVDA", "shares": 1.0}], "watch_list": [], "targets": {}, "strategy": ""}
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="missing keys"):
        load_portfolio(p)


def test_iter_watchlist_returns_target(valid_portfolio_file):
    p = load_portfolio(valid_portfolio_file)
    pairs = list(iter_watchlist(p))
    bro = next((t, tgt) for t, tgt in pairs if t == "BRO")
    assert bro[1] == pytest.approx(65.0)


def test_iter_watchlist_missing_target_is_none(valid_portfolio_file):
    p = load_portfolio(valid_portfolio_file)
    pairs = dict(iter_watchlist(p))
    assert pairs["BAC"] is None


def test_load_portfolio_parses_entry_types(tmp_path):
    data = {
        "holdings": [],
        "watch_list": ["AVGO", "ANET"],
        "targets": {"AVGO": 440.0, "ANET": 164.0},
        "entry_types": {"AVGO": "breakout", "ANET": "pullback"},
        "strategy": "test",
    }
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    portfolio = load_portfolio(p)
    assert portfolio.entry_types["AVGO"] == "breakout"
    assert portfolio.entry_types["ANET"] == "pullback"


def test_load_portfolio_entry_types_defaults_to_empty(valid_portfolio_file):
    portfolio = load_portfolio(valid_portfolio_file)
    assert portfolio.entry_types == {}


def test_persist_watchlist_additions_writes_entry_types(tmp_path):
    from portfolio_lib.loader import persist_watchlist_additions
    data = {
        "holdings": [],
        "watch_list": ["ANET"],
        "targets": {"ANET": 164.0},
        "entry_types": {"ANET": "pullback"},
        "strategy": "test",
    }
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    added = persist_watchlist_additions(
        p,
        ["MRVL"],
        targets={"MRVL": 80.0},
        entry_types={"MRVL": "breakout"},
    )

    assert added == ["MRVL"]
    result = load_portfolio(p)
    assert result.entry_types["MRVL"] == "breakout"
    assert result.entry_types["ANET"] == "pullback"  # existing preserved


def test_persist_watchlist_additions_no_entry_types_leaves_existing(tmp_path):
    from portfolio_lib.loader import persist_watchlist_additions
    data = {
        "holdings": [],
        "watch_list": [],
        "targets": {},
        "entry_types": {},
        "strategy": "test",
    }
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    persist_watchlist_additions(p, ["PLTR"], targets={"PLTR": 160.0})

    result = load_portfolio(p)
    # No entry_type set — R7 will default to pullback via fallback
    assert "PLTR" not in result.entry_types
