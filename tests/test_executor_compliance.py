"""Integration tests — compliance gate in stage_pending_order.

These tests verify that compliance-blocked orders never reach Alpaca and that
the executor's qty cap and return value behave correctly.

Alpaca client is monkeypatched so no real API calls are made.
"""
import json
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


# ── R7: Entry price gate blocks Alpaca submission ─────────────────────────────

def test_r7_pullback_price_above_target_blocked(tmp_path):
    """Watchlist pullback entry should not reach Alpaca when price is above target."""
    p = Portfolio(
        holdings=(),
        watch_list=("ANET",),
        targets={"ANET": 164.0},
        entry_types={"ANET": "pullback"},
        strategy="test",
        cash_balance=5_000.0,
    )

    with patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="ANET",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=p.cash_balance,
            current_price=170.68,
            target_price=164.0,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
            prices={"ANET": 170.68},
        )

    assert blocked == "R7"
    mock_submit.assert_not_called()


def test_r7_breakout_within_window_passes(tmp_path):
    """Breakout entry within 3% of trigger should reach Alpaca at current price."""
    p = Portfolio(
        holdings=(),
        watch_list=("AVGO",),
        targets={"AVGO": 440.0},
        entry_types={"AVGO": "breakout"},
        strategy="test",
        cash_balance=5_000.0,
    )

    submitted_orders = []

    def fake_submit(order, shares_override, results_dir, *, portfolio=None, prices=None):
        submitted_orders.append(order)
        return {**order, "alpaca_order_id": "fake-id", "status": "submitted"}

    with patch("portfolio_lib.executor.submit_order", side_effect=fake_submit):
        blocked = stage_pending_order(
            ticker="AVGO",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=p.cash_balance,
            current_price=450.0,  # ~2.3% above $440 trigger — within window
            target_price=440.0,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
            prices={"AVGO": 450.0},
        )

    assert blocked is None
    assert len(submitted_orders) == 1
    # breakout entry: limit_price should be current price, not the target
    assert submitted_orders[0]["limit_price"] == pytest.approx(450.0)


def test_r7_breakout_stale_blocked(tmp_path):
    """Breakout entry >3% above trigger should be hard-blocked."""
    p = Portfolio(
        holdings=(),
        watch_list=("AVGO",),
        targets={"AVGO": 440.0},
        entry_types={"AVGO": "breakout"},
        strategy="test",
        cash_balance=5_000.0,
    )

    with patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="AVGO",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=p.cash_balance,
            current_price=484.0,
            target_price=440.0,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
            prices={"AVGO": 484.0},
        )

    assert blocked == "R7"
    mock_submit.assert_not_called()


def test_r7_breakout_below_trigger_blocked(tmp_path):
    """Breakout entry should not reach Alpaca when price is below the trigger."""
    p = Portfolio(
        holdings=(),
        watch_list=("AVGO",),
        targets={"AVGO": 440.0},
        entry_types={"AVGO": "breakout"},
        strategy="test",
        cash_balance=5_000.0,
    )

    with patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="AVGO",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=p.cash_balance,
            current_price=430.0,
            target_price=440.0,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
            prices={"AVGO": 430.0},
        )

    assert blocked == "R7"
    mock_submit.assert_not_called()


# ── LLM share recommendation extraction (Issue 3) ────────────────────────────

from portfolio_lib.executor import _extract_llm_share_recommendation


def test_extract_position_sizing_line():
    text = "**Position Sizing**: 3 shares at approximately $450/share = ~$1,350 total"
    assert _extract_llm_share_recommendation(text) == 3.0


def test_extract_buy_n_shares_of_ticker():
    text = "Buy 2 shares of AVGO at market price (~$450/share)"
    assert _extract_llm_share_recommendation(text) == 2.0


def test_extract_returns_none_for_empty_text():
    assert _extract_llm_share_recommendation("") is None
    assert _extract_llm_share_recommendation(None) is None


def test_extract_returns_none_when_no_pattern():
    assert _extract_llm_share_recommendation("HOLD — no position changes recommended") is None


def test_extract_caps_at_max_auto_shares():
    from portfolio_lib.executor import _MAX_AUTO_SHARES
    text = f"**Position Sizing**: {_MAX_AUTO_SHARES + 5} shares at $100/share"
    assert _extract_llm_share_recommendation(text) is None


def test_stage_uses_llm_shares_for_sizing(tmp_path):
    """When trader_plan_text contains a share recommendation, the order uses that qty."""
    p = _portfolio(cash=2_175.65)
    submitted_orders = []

    def fake_submit(order, shares_override, results_dir, *, portfolio=None, prices=None):
        submitted_orders.append(order)
        return {**order, "alpaca_order_id": "fake-id", "status": "submitted"}

    plan_text = "**Position Sizing**: 3 shares at approximately $450/share = ~$1,350 total"

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=False), \
         patch("portfolio_lib.executor.submit_order", side_effect=fake_submit):
        stage_pending_order(
            ticker="AVGO",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=p.cash_balance,
            current_price=450.0,
            target_price=None,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
            trader_plan_text=plan_text,
        )

    assert len(submitted_orders) == 1
    assert submitted_orders[0]["suggested_shares"] == 3.0
    assert submitted_orders[0]["suggested_notional"] == pytest.approx(3.0 * 450.0)


def test_stage_falls_back_to_pct_when_no_llm_shares(tmp_path):
    """Without trader_plan_text, sizing falls back to 10% of cash."""
    p = _portfolio(cash=2_175.65)
    submitted_orders = []

    def fake_submit(order, shares_override, results_dir, *, portfolio=None, prices=None):
        submitted_orders.append(order)
        return {**order, "alpaca_order_id": "fake-id", "status": "submitted"}

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=False), \
         patch("portfolio_lib.executor.submit_order", side_effect=fake_submit):
        stage_pending_order(
            ticker="NVDA",
            decision="BUY",
            kind="HOLDING",
            cash_balance=p.cash_balance,
            current_price=212.0,
            target_price=None,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
            trader_plan_text=None,
        )

    assert len(submitted_orders) == 1
    expected = round(2_175.65 * 0.10 / 212.0, 4)
    assert submitted_orders[0]["suggested_shares"] == pytest.approx(expected)


# ── R7 requires prices dict to contain the watchlist ticker (regression) ─────

def test_r7_pullback_blocked_when_prices_dict_contains_watchlist_ticker(tmp_path):
    """R7 must fire for watchlist tickers when their price is in the prices dict.

    Regression: analyze_portfolio.py previously only pre-fetched prices for
    holdings, so prices.get('ANET') was None for watchlist tickers and R7
    silently skipped, allowing orders to pass through at wrong prices.
    """
    p = Portfolio(
        holdings=(),
        watch_list=("ANET",),
        targets={"ANET": 164.0},
        entry_types={"ANET": "pullback"},
        strategy="test",
        cash_balance=5_000.0,
    )

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=False), \
         patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="ANET",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=p.cash_balance,
            current_price=175.33,
            target_price=164.0,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
            prices={"ANET": 175.33},  # watchlist ticker present in prices dict
        )

    assert blocked == "R7", f"Expected R7, got {blocked!r}"
    mock_submit.assert_not_called()


def test_r7_skipped_when_watchlist_ticker_missing_from_prices_dict(tmp_path):
    """Without the ticker in prices dict, R7 cannot evaluate — order proceeds."""
    p = Portfolio(
        holdings=(),
        watch_list=("ANET",),
        targets={"ANET": 164.0},
        entry_types={"ANET": "pullback"},
        strategy="test",
        cash_balance=5_000.0,
    )

    submitted_orders = []

    def fake_submit(order, shares_override, results_dir, *, portfolio=None, prices=None):
        submitted_orders.append(order)
        return {**order, "alpaca_order_id": "fake-id", "status": "submitted"}

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=False), \
         patch("portfolio_lib.executor.submit_order", side_effect=fake_submit):
        blocked = stage_pending_order(
            ticker="ANET",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=p.cash_balance,
            current_price=175.33,
            target_price=164.0,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=p,
            prices={},  # ANET not in prices — R7 skips, order passes through
        )

    # R7 skips (no price data), order proceeds — documents the fallback behaviour
    assert blocked is None
    assert len(submitted_orders) == 1


# ── Alpaca compliance portfolio (Issue 2) ────────────────────────────────────

def _write_alpaca_portfolio(path: Path, holdings, cash: float, position_caps=None) -> None:
    data = {
        "holdings": [
            {"ticker": h.ticker, "entry": h.entry, "shares": h.shares}
            for h in holdings
        ],
        "watch_list": [],
        "targets": {},
        "strategy": "test",
        "cash_balance": cash,
        "position_caps": position_caps or {},
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def test_compliance_uses_alpaca_holdings_not_fidelity(tmp_path):
    """R5a must block based on Alpaca share count, not Fidelity's."""
    # Fidelity has 0 AVGO shares — no cap would fire against Fidelity.
    # Alpaca has already accumulated 3 shares of AVGO.
    # Fidelity position_caps says AVGO max = 3.
    fidelity = Portfolio(
        holdings=(),
        watch_list=("AVGO",),
        targets={"AVGO": 440.0},
        entry_types={"AVGO": "breakout"},
        strategy="test",
        cash_balance=2_175.65,
        position_caps={"AVGO": 3},
    )
    alpaca_path = tmp_path / "alpaca_portfolio.json"
    alpaca_holding = Holding(ticker="AVGO", entry=448.0, shares=3.0)
    _write_alpaca_portfolio(alpaca_path, [alpaca_holding], cash=828.0)

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=False), \
         patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="AVGO",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=fidelity.cash_balance,
            current_price=450.0,  # within breakout window
            target_price=440.0,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=fidelity,
            alpaca_portfolio_path=alpaca_path,
            prices={"AVGO": 450.0},
        )

    # Should be blocked by R5a (Alpaca already at 3-share cap)
    assert blocked == "R5"
    mock_submit.assert_not_called()


def test_compliance_fidelity_caps_applied_to_alpaca(tmp_path):
    """position_caps from Fidelity must be inherited even if absent from alpaca_portfolio.json."""
    fidelity = Portfolio(
        holdings=(),
        watch_list=("AVGO",),
        targets={"AVGO": 440.0},
        entry_types={"AVGO": "breakout"},
        strategy="test",
        cash_balance=2_175.65,
        position_caps={"AVGO": 2},  # Fidelity cap: max 2 shares
    )
    alpaca_path = tmp_path / "alpaca_portfolio.json"
    # Alpaca has 2 shares, no position_caps in its file
    alpaca_holding = Holding(ticker="AVGO", entry=448.0, shares=2.0)
    _write_alpaca_portfolio(alpaca_path, [alpaca_holding], cash=1_200.0, position_caps={})

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=False), \
         patch("portfolio_lib.executor.submit_order") as mock_submit:
        blocked = stage_pending_order(
            ticker="AVGO",
            decision="BUY",
            kind="WATCHLIST",
            cash_balance=fidelity.cash_balance,
            current_price=450.0,
            target_price=440.0,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=fidelity,
            alpaca_portfolio_path=alpaca_path,
            prices={"AVGO": 450.0},
        )

    # Fidelity cap=2, Alpaca has 2 → R5a should block
    assert blocked == "R5"
    mock_submit.assert_not_called()


def test_compliance_falls_back_to_fidelity_if_alpaca_file_missing(tmp_path):
    """If alpaca_portfolio.json doesn't exist, fall back to Fidelity compliance."""
    fidelity = Portfolio(
        holdings=(_nvda_holding(),),
        watch_list=(),
        targets={},
        entry_types={},
        strategy="test",
        cash_balance=2_175.65,
    )
    missing_path = tmp_path / "alpaca_portfolio.json"  # does not exist

    submitted_orders = []

    def fake_submit(order, shares_override, results_dir, *, portfolio=None, prices=None):
        submitted_orders.append(order)
        return {**order, "alpaca_order_id": "fake-id", "status": "submitted"}

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=False), \
         patch("portfolio_lib.executor.submit_order", side_effect=fake_submit):
        blocked = stage_pending_order(
            ticker="NVDA",
            decision="BUY",
            kind="HOLDING",
            cash_balance=fidelity.cash_balance,
            current_price=212.0,
            target_price=None,
            report_path=None,
            results_dir=tmp_path / "Analysis",
            portfolio=fidelity,
            alpaca_portfolio_path=missing_path,
        )

    # Falls back to Fidelity — NVDA has no cap, should pass
    assert blocked is None
    assert len(submitted_orders) == 1


# ── Duplicate same-day order guard ───────────────────────────────────────────

def test_duplicate_same_side_order_skips_alpaca(tmp_path):
    """If Alpaca already has an open BUY for this ticker, auto-execute should skip."""
    p = _portfolio([_nvda_holding()], cash=2_175.65)

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=True), \
         patch("portfolio_lib.executor.submit_order") as mock_submit:
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

    assert blocked is None          # not a compliance block — just a skip
    mock_submit.assert_not_called()


def test_no_duplicate_allows_submission(tmp_path):
    """If no open same-side order exists, auto-execute should proceed normally."""
    p = _portfolio([_nvda_holding()], cash=2_175.65)
    submitted_orders = []

    def fake_submit(order, shares_override, results_dir, *, portfolio=None, prices=None):
        submitted_orders.append(order)
        return {**order, "alpaca_order_id": "fake-id", "status": "submitted"}

    with patch("portfolio_lib.executor._open_same_side_order_exists", return_value=False), \
         patch("portfolio_lib.executor.submit_order", side_effect=fake_submit):
        stage_pending_order(
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

    assert len(submitted_orders) == 1


def test_same_side_check_failure_returns_false():
    """If the Alpaca API call inside the helper raises, it swallows and returns False."""
    # TradingClient is lazily imported inside the helper — patch via its source module
    with patch("alpaca.trading.client.TradingClient", side_effect=RuntimeError("no keys")):
        from portfolio_lib.executor import _open_same_side_order_exists
        result = _open_same_side_order_exists("NVDA", "BUY")
    assert result is False


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
