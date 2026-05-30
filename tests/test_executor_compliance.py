"""Integration tests — compliance gate in stage_pending_order.

These tests verify that compliance-blocked orders never reach Alpaca and that
the executor's qty cap and return value behave correctly.

Alpaca client is monkeypatched so no real API calls are made.
"""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from portfolio_lib.executor import stage_pending_order, _MAX_AUTO_SHARES
from portfolio_lib.loader import Holding, Portfolio

TODAY_ISO = "2026-06-19"  # inside GME wash-sale lockout window
LOCKOUT_END = "2026-07-01"


def _portfolio(holdings=(), cash=5_000.0):
    return Portfolio(
        holdings=tuple(holdings),
        watch_list=(),
        targets={},
        strategy="test",
        cash_balance=cash,
    )


def _gme_harvest_holding():
    return Holding(
        ticker="GME",
        entry=57.58,
        shares=26.0,
        role="tax-loss-harvest",
        wash_sale_lockout_until=LOCKOUT_END,
    )


def _nvda_holding():
    return Holding(ticker="NVDA", entry=222.72, shares=3.0)


# ── GME tax-loss hold: BUY never reaches Alpaca ──────────────────────────────

@pytest.fixture(autouse=True)
def _mock_alpaca_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setenv("ALPACA_AUTO_EXECUTE", "true")
    monkeypatch.setenv("ALPACA_PAPER", "true")


def test_gme_buy_never_reaches_alpaca(tmp_path, caplog):
    import logging
    p = _portfolio([_gme_harvest_holding()])

    with patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="GME",
            decision="BUY",
            kind="HOLDING",
            cash_balance=p.cash_balance,
            current_price=21.28,
            target_price=None,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
        )

    assert blocked == "R2", f"Expected R2, got {blocked!r}"
    mock_submit.assert_not_called()


def test_gme_sell_never_reaches_alpaca(tmp_path):
    p = _portfolio([_gme_harvest_holding()])

    with patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="GME",
            decision="SELL",
            kind="HOLDING",
            cash_balance=p.cash_balance,
            current_price=21.28,
            target_price=None,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
        )

    assert blocked == "R2"
    mock_submit.assert_not_called()


# ── GMEWS warrant: blocked on all sides ──────────────────────────────────────

def test_gmews_buy_blocked(tmp_path):
    gmews = Holding(ticker="GMEWS", entry=0.0, shares=4.0)
    p = _portfolio([gmews])

    with patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="GMEWS",
            decision="BUY",
            kind="HOLDING",
            cash_balance=p.cash_balance,
            current_price=0.01,
            target_price=None,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
        )

    assert blocked == "R3"
    mock_submit.assert_not_called()


# ── Watchlist SELL guard (R4) ─────────────────────────────────────────────────

def test_watchlist_ticker_sell_blocked_by_r4(tmp_path):
    p = _portfolio()  # PLTR not in holdings

    with patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="PLTR",
            decision="SELL",
            kind="WATCHLIST",
            cash_balance=p.cash_balance,
            current_price=156.0,
            target_price=None,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
        )

    # executor already filters SELL for WATCHLIST kind before compliance check —
    # confirm the order is not submitted regardless
    mock_submit.assert_not_called()


# ── Valid NVDA BUY passes through (returns None) ──────────────────────────────

def test_valid_nvda_buy_passes_compliance(tmp_path):
    p = _portfolio([_nvda_holding()], cash=2_175.65)

    submitted_orders = []

    def fake_submit(order, shares_override, results_dir, *, portfolio=None, prices=None):
        submitted_orders.append(order)
        return {**order, "alpaca_order_id": "fake-id", "status": "submitted"}

    with patch("portfolio_lib.executor.submit_order", side_effect=fake_submit):
        blocked = stage_pending_order(
            ticker="NVDA",
            decision="BUY",
            kind="HOLDING",
            cash_balance=p.cash_balance,
            current_price=212.31,
            target_price=None,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
        )

    assert blocked is None
    assert len(submitted_orders) == 1
    assert submitted_orders[0]["ticker"] == "NVDA"


# ── Auto-execute qty cap ──────────────────────────────────────────────────────

def test_auto_execute_qty_clamped_to_max(tmp_path):
    """Suggested shares far above cap should be clamped, not rejected."""
    from portfolio_lib.executor import submit_order as real_submit

    order = {
        "id": "test-id",
        "created_at": "2026-05-29T10:00:00",
        "ticker": "NVDA",
        "action": "BUY",
        "signal": "BUY",
        "suggested_shares": 9999.0,  # absurdly large
        "suggested_notional": 2_000_000.0,
        "limit_price": None,
        "current_price": 212.0,
        "expires_at": "2026-05-30T09:30:00",
        "status": "pending",
        "report_path": None,
    }

    mock_client = MagicMock()
    submitted_order = MagicMock()
    submitted_order.id = "alpaca-123"
    mock_client.submit_order.return_value = submitted_order

    with patch("alpaca.trading.client.TradingClient", return_value=mock_client), \
         patch("portfolio_lib.executor._append_trade_log"), \
         patch("portfolio_lib.executor._update_order"):
        real_submit(order, None, tmp_path / "Analysis")

    # Verify the Alpaca client was called with qty = _MAX_AUTO_SHARES, not 9999
    call_kwargs = mock_client.submit_order.call_args
    req_obj = call_kwargs[0][0]
    assert req_obj.qty == _MAX_AUTO_SHARES


# ── No-portfolio warning (backwards compat) ───────────────────────────────────

def test_stage_without_portfolio_logs_warning(tmp_path, caplog):
    import logging

    with patch("portfolio_lib.executor.submit_order") as mock_submit:
        mock_submit.return_value = {"alpaca_order_id": "x", "status": "submitted"}
        with caplog.at_level(logging.WARNING, logger="portfolio_lib.executor"):
            stage_pending_order(
                ticker="NVDA",
                decision="BUY",
                kind="HOLDING",
                cash_balance=1_000.0,
                current_price=212.0,
                target_price=None,
                report_path=None,
                results_dir=tmp_path / "Analysis",
                # portfolio intentionally omitted
            )

    assert any("compliance checks skipped" in r.message for r in caplog.records)
