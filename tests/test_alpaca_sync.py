"""Unit tests for portfolio_lib.alpaca_sync.

No Alpaca API calls are made — all network access is either mocked or exercised
only through dry_run=True paths.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from portfolio_lib.alpaca_sync import (
    load_alpaca_portfolio,
    reconcile_with_alpaca,
    reset_to_fidelity,
    update_after_trade,
    _is_tradeable,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

FIDELITY_DATA = {
    "owner": "Test",
    "last_updated": "2026-05-29",
    "cash_balance": 2175.65,
    "holdings": [
        {"ticker": "GMEWS", "entry": 0.0,    "shares": 4.0},
        {"ticker": "GME",   "entry": 57.58,  "shares": 26.0, "role": "tax-loss-harvest",
         "wash_sale_lockout_until": "2026-06-19"},
        {"ticker": "NVDA",  "entry": 222.72, "shares": 3.0,  "acquired_date": "2026-05-20"},
    ],
    "open_orders": [],
    "watch_list": ["ANET", "AVGO", "PLTR"],
    "targets": {"ANET": 164.0, "AVGO": 440.0, "PLTR": 160.0},
    "strategy": "test",
}


@pytest.fixture()
def fidelity_json(tmp_path: Path) -> Path:
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps(FIDELITY_DATA), encoding="utf-8")
    return p


@pytest.fixture()
def alpaca_json(tmp_path: Path) -> Path:
    return tmp_path / "alpaca_portfolio.json"


# ── _is_tradeable ─────────────────────────────────────────────────────────────

def test_tradeable_nvda():
    assert _is_tradeable({"ticker": "NVDA", "entry": 222.72, "shares": 3.0}) is True


def test_not_tradeable_warrant():
    assert _is_tradeable({"ticker": "GMEWS", "entry": 0.0, "shares": 4.0}) is False


def test_not_tradeable_harvest():
    h = {"ticker": "GME", "entry": 57.58, "shares": 26.0, "role": "tax-loss-harvest"}
    assert _is_tradeable(h) is False


# ── load_alpaca_portfolio ─────────────────────────────────────────────────────

def test_load_auto_init_when_missing(fidelity_json, alpaca_json):
    assert not alpaca_json.exists()
    data = load_alpaca_portfolio(alpaca_json, fidelity_json)
    assert alpaca_json.exists()
    assert data["cash_balance"] == 2175.65
    assert len(data["holdings"]) == 3
    assert data["_source"] == "auto-initialised from portfolio.json"


def test_load_returns_existing_file(fidelity_json, alpaca_json):
    alpaca_json.write_text(json.dumps({"cash_balance": 999.0, "holdings": []}), encoding="utf-8")
    data = load_alpaca_portfolio(alpaca_json, fidelity_json)
    assert data["cash_balance"] == 999.0  # existing file, not Fidelity


# ── reset_to_fidelity (dry_run) ───────────────────────────────────────────────

def test_reset_dry_run_writes_nothing(fidelity_json, alpaca_json):
    reset_to_fidelity(fidelity_json, alpaca_json, dry_run=True)
    assert not alpaca_json.exists()


def test_reset_dry_run_no_api_calls(fidelity_json, alpaca_json):
    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client") as mock_client:
        reset_to_fidelity(fidelity_json, alpaca_json, dry_run=True)
    mock_client.assert_not_called()


def test_reset_writes_alpaca_portfolio(fidelity_json, alpaca_json):
    mock_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "alpaca-reset-123"
    mock_client.submit_order.return_value = mock_order

    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client", return_value=mock_client):
        reset_to_fidelity(fidelity_json, alpaca_json)

    assert alpaca_json.exists()
    data = json.loads(alpaca_json.read_text())
    assert data["cash_balance"] == 2175.65
    assert len(data["holdings"]) == 3
    assert "_alpaca_reset_at" in data
    assert data["_source"] == "auto-synced from portfolio.json via reset_to_fidelity()"


def test_reset_cancels_orders_and_closes_positions(fidelity_json, alpaca_json):
    mock_client = MagicMock()
    mock_client.submit_order.return_value = MagicMock(id="x")

    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client", return_value=mock_client):
        reset_to_fidelity(fidelity_json, alpaca_json)

    mock_client.cancel_orders.assert_called_once()
    mock_client.close_all_positions.assert_called_once_with(cancel_orders=True)


def test_reset_buys_only_tradeable_holdings(fidelity_json, alpaca_json):
    """Should submit BUY for NVDA only — not GMEWS (warrant) or GME (harvest)."""
    mock_client = MagicMock()
    mock_client.submit_order.return_value = MagicMock(id="x")

    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client", return_value=mock_client):
        reset_to_fidelity(fidelity_json, alpaca_json)

    call_args_list = mock_client.submit_order.call_args_list
    assert len(call_args_list) == 1
    req = call_args_list[0][0][0]
    assert req.symbol == "NVDA"
    assert req.qty == 3.0


# ── update_after_trade ────────────────────────────────────────────────────────

def _make_alpaca_file(path: Path, holdings=None, cash=2175.65) -> None:
    data = {
        "cash_balance": cash,
        "holdings": holdings or [],
        "watch_list": [],
        "targets": {},
        "strategy": "test",
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def test_buy_new_position(alpaca_json):
    _make_alpaca_file(alpaca_json, cash=2000.0)
    order = {
        "ticker": "NVDA",
        "action": "BUY",
        "shares_executed": 2.0,
        "current_price": 212.0,
        "submitted_at": "2026-05-29T10:00:00",
    }
    update_after_trade(alpaca_json, order)

    data = json.loads(alpaca_json.read_text())
    nvda = next(h for h in data["holdings"] if h["ticker"] == "NVDA")
    assert nvda["shares"] == 2.0
    assert nvda["entry"] == 212.0
    assert data["cash_balance"] == pytest.approx(2000.0 - 2.0 * 212.0)


def test_buy_existing_position_weighted_avg(alpaca_json):
    _make_alpaca_file(
        alpaca_json,
        holdings=[{"ticker": "NVDA", "entry": 200.0, "shares": 2.0}],
        cash=1000.0,
    )
    order = {
        "ticker": "NVDA",
        "action": "BUY",
        "shares_executed": 2.0,
        "current_price": 220.0,
    }
    update_after_trade(alpaca_json, order)

    data = json.loads(alpaca_json.read_text())
    nvda = next(h for h in data["holdings"] if h["ticker"] == "NVDA")
    assert nvda["shares"] == 4.0
    assert nvda["entry"] == pytest.approx(210.0)  # (200*2 + 220*2) / 4
    assert data["cash_balance"] == pytest.approx(1000.0 - 2.0 * 220.0)


def test_sell_partial(alpaca_json):
    _make_alpaca_file(
        alpaca_json,
        holdings=[{"ticker": "NVDA", "entry": 212.0, "shares": 3.0}],
        cash=500.0,
    )
    order = {
        "ticker": "NVDA",
        "action": "SELL",
        "shares_executed": 1.0,
        "current_price": 230.0,
    }
    update_after_trade(alpaca_json, order)

    data = json.loads(alpaca_json.read_text())
    nvda = next(h for h in data["holdings"] if h["ticker"] == "NVDA")
    assert nvda["shares"] == pytest.approx(2.0)
    assert data["cash_balance"] == pytest.approx(500.0 + 1.0 * 230.0)


def test_sell_full_removes_holding(alpaca_json):
    _make_alpaca_file(
        alpaca_json,
        holdings=[{"ticker": "NVDA", "entry": 212.0, "shares": 2.0}],
        cash=500.0,
    )
    order = {
        "ticker": "NVDA",
        "action": "SELL",
        "shares_executed": 2.0,
        "current_price": 230.0,
    }
    update_after_trade(alpaca_json, order)

    data = json.loads(alpaca_json.read_text())
    tickers = [h["ticker"] for h in data["holdings"]]
    assert "NVDA" not in tickers
    assert data["cash_balance"] == pytest.approx(500.0 + 2.0 * 230.0)


def test_update_missing_file_logs_warning(alpaca_json, caplog):
    import logging
    assert not alpaca_json.exists()
    with caplog.at_level(logging.WARNING, logger="portfolio_lib.alpaca_sync"):
        update_after_trade(alpaca_json, {"ticker": "NVDA", "action": "BUY",
                                         "shares_executed": 1.0, "current_price": 212.0})
    assert not alpaca_json.exists()
    assert any("not found" in r.message for r in caplog.records)


# ── reconcile_with_alpaca ─────────────────────────────────────────────────────

def _mock_alpaca_client(positions: list[tuple], cash: str = "1554.67") -> MagicMock:
    """Build a mock TradingClient with preset positions and account cash."""
    client = MagicMock()
    mock_account = MagicMock()
    mock_account.cash = cash
    client.get_account.return_value = mock_account

    mock_positions = []
    for ticker, qty, entry in positions:
        pos = MagicMock()
        pos.symbol = ticker
        pos.qty = str(qty)
        pos.avg_entry_price = str(entry)
        mock_positions.append(pos)
    client.get_all_positions.return_value = mock_positions
    return client


def test_reconcile_writes_live_positions(alpaca_json):
    """Live NVDA position replaces whatever was in alpaca_portfolio.json."""
    mock_client = _mock_alpaca_client([("NVDA", 2.5, 220.0)], cash="828.00")

    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client", return_value=mock_client):
        reconcile_with_alpaca(alpaca_json)

    data = json.loads(alpaca_json.read_text())
    nvda = next(h for h in data["holdings"] if h["ticker"] == "NVDA")
    assert nvda["shares"] == pytest.approx(2.5)
    assert nvda["entry"] == pytest.approx(220.0)
    assert data["cash_balance"] == pytest.approx(828.0)
    assert data["_source"] == "reconciled from Alpaca paper account"
    assert "_reconciled_at" in data


def test_reconcile_only_live_positions_in_holdings(alpaca_json):
    """Holdings must contain only live Alpaca positions — no Fidelity-only injection."""
    mock_client = _mock_alpaca_client([("NVDA", 3.0, 222.72)])

    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client", return_value=mock_client):
        reconcile_with_alpaca(alpaca_json)

    data = json.loads(alpaca_json.read_text())
    tickers = {h["ticker"] for h in data["holdings"]}
    assert tickers == {"NVDA"}


def test_reconcile_empty_positions_yields_empty_holdings(alpaca_json):
    """When Alpaca holds nothing, holdings should be empty — no Fidelity bleeding in."""
    mock_client = _mock_alpaca_client([])

    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client", return_value=mock_client):
        reconcile_with_alpaca(alpaca_json)

    data = json.loads(alpaca_json.read_text())
    assert data["holdings"] == []


def test_reconcile_preserves_holding_level_doctrine(alpaca_json):
    """role and wash_sale_lockout_until are preserved from the existing Alpaca file."""
    # Pre-populate alpaca_portfolio.json with a GME holding that carries doctrine fields.
    alpaca_json.write_text(json.dumps({
        "holdings": [
            {"ticker": "GME", "entry": 42.42, "shares": 26.0,
             "role": "tax-loss-harvest", "wash_sale_lockout_until": "2026-06-19"},
        ],
        "watch_list": [], "targets": {}, "strategy": "test", "cash_balance": 100.0,
    }), encoding="utf-8")

    # Alpaca live API also returns GME (the bot holds it)
    mock_client = _mock_alpaca_client([("GME", 26.0, 42.42)])

    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client", return_value=mock_client):
        reconcile_with_alpaca(alpaca_json)

    data = json.loads(alpaca_json.read_text())
    gme = next(h for h in data["holdings"] if h["ticker"] == "GME")
    assert gme.get("role") == "tax-loss-harvest"
    assert gme.get("wash_sale_lockout_until") == "2026-06-19"


def test_reconcile_preserves_own_doctrine_fields(alpaca_json):
    """position_caps, entry_types, targets, and watch_list are preserved from alpaca_portfolio.json."""
    alpaca_json.write_text(json.dumps({
        "holdings": [],
        "watch_list": ["KLAC"],
        "targets": {"KLAC": 250.0},
        "entry_types": {"KLAC": "pullback"},
        "position_caps": {"PLTR": 16, "KLAC": 2},
        "strategy": "test",
        "cash_balance": 5000.0,
    }), encoding="utf-8")

    mock_client = _mock_alpaca_client([("PLTR", 16.0, 130.45)])

    with patch("portfolio_lib.alpaca_sync._alpaca_paper_client", return_value=mock_client):
        reconcile_with_alpaca(alpaca_json)

    data = json.loads(alpaca_json.read_text())
    assert data.get("position_caps") == {"PLTR": 16, "KLAC": 2}
    assert data.get("entry_types") == {"KLAC": "pullback"}
    assert data.get("watch_list") == ["KLAC"]
    assert data.get("targets") == {"KLAC": 250.0}


def test_reconcile_raises_when_keys_missing(alpaca_json):
    """RuntimeError from _alpaca_paper_client propagates so the caller can catch it."""
    with patch(
        "portfolio_lib.alpaca_sync._alpaca_paper_client",
        side_effect=RuntimeError("no keys"),
    ):
        with pytest.raises(RuntimeError, match="no keys"):
            reconcile_with_alpaca(alpaca_json)

    assert not alpaca_json.exists()  # file not written on failure


def test_buy_uses_suggested_notional_when_no_price(alpaca_json):
    _make_alpaca_file(alpaca_json, cash=2000.0)
    order = {
        "ticker": "PLTR",
        "action": "BUY",
        "shares_executed": 1.0,
        "current_price": None,
        "limit_price": None,
        "suggested_notional": 200.0,
    }
    update_after_trade(alpaca_json, order)
    data = json.loads(alpaca_json.read_text())
    assert data["cash_balance"] == pytest.approx(1800.0)
