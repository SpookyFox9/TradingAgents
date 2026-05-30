"""Unit tests for portfolio_lib.compliance — R1 through R6."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from portfolio_lib.compliance import (
    ComplianceResult,
    check_order,
    is_blocked_ticker,
)
from portfolio_lib.loader import Holding, Portfolio


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _portfolio(
    *holdings: Holding,
    cash: float = 5_000.0,
    watch_list=(),
) -> Portfolio:
    return Portfolio(
        holdings=tuple(holdings),
        watch_list=tuple(watch_list),
        targets={},
        strategy="test",
        cash_balance=cash,
    )


def _holding(
    ticker: str,
    entry: float = 100.0,
    shares: float = 1.0,
    role=None,
    wash_sale_lockout_until=None,
) -> Holding:
    return Holding(
        ticker=ticker,
        entry=entry,
        shares=shares,
        role=role,
        wash_sale_lockout_until=wash_sale_lockout_until,
    )


TODAY = date(2026, 5, 29)
FUTURE = (TODAY + timedelta(days=10)).isoformat()
PAST = (TODAY - timedelta(days=1)).isoformat()


# ── R1: Wash-sale lockout ──────────────────────────────────────────────────────

def test_r1_buy_blocked_during_lockout():
    h = _holding("GME", wash_sale_lockout_until=FUTURE)
    p = _portfolio(h)
    result = check_order("GME", "BUY", p, today=TODAY)
    assert result == ComplianceResult(False, "R1", result.reason)
    assert "wash-sale lockout" in result.reason


def test_r1_buy_allowed_after_lockout():
    h = _holding("GME", wash_sale_lockout_until=PAST)
    p = _portfolio(h)
    result = check_order("GME", "BUY", p, today=TODAY)
    assert result.allowed


def test_r1_sell_never_blocked_by_lockout():
    h = _holding("GME", shares=5.0, wash_sale_lockout_until=FUTURE)
    p = _portfolio(h)
    result = check_order("GME", "SELL", p, today=TODAY)
    assert result.allowed


def test_r1_buy_allowed_when_no_lockout_field():
    h = _holding("NVDA")
    p = _portfolio(h)
    result = check_order("NVDA", "BUY", p, today=TODAY)
    assert result.allowed


# ── R2: Tax-loss harvest hold ─────────────────────────────────────────────────

def test_r2_buy_blocked_on_harvest_hold():
    h = _holding("GME", role="tax-loss-harvest")
    p = _portfolio(h)
    result = check_order("GME", "BUY", p, today=TODAY)
    assert result == ComplianceResult(False, "R2", result.reason)


def test_r2_sell_blocked_on_harvest_hold():
    h = _holding("GME", shares=26.0, role="tax-loss-harvest")
    p = _portfolio(h)
    result = check_order("GME", "SELL", p, today=TODAY)
    assert result == ComplianceResult(False, "R2", result.reason)


def test_r2_normal_holding_not_blocked():
    h = _holding("NVDA")
    p = _portfolio(h)
    result = check_order("NVDA", "BUY", p, today=TODAY)
    assert result.allowed


# ── R3: Warrant detection ─────────────────────────────────────────────────────

def test_r3_zero_entry_warrant_blocked():
    h = _holding("GMEWS", entry=0.0)
    p = _portfolio(h)
    result = check_order("GMEWS", "BUY", p, today=TODAY)
    assert result == ComplianceResult(False, "R3", result.reason)


def test_r3_w_suffix_blocked():
    p = _portfolio()
    result = check_order("NVDAW", "BUY", p, today=TODAY)
    assert result == ComplianceResult(False, "R3", result.reason)


def test_r3_ws_suffix_blocked():
    p = _portfolio()
    result = check_order("GMEWS", "SELL", p, today=TODAY)
    assert result == ComplianceResult(False, "R3", result.reason)


def test_r3_wt_suffix_blocked():
    p = _portfolio()
    result = check_order("SPCWT", "BUY", p, today=TODAY)
    assert result == ComplianceResult(False, "R3", result.reason)


def test_r3_normal_ticker_not_flagged():
    p = _portfolio()
    result = check_order("NVDA", "BUY", p, today=TODAY)
    # R4 may block (no position), but not R3
    assert result.rule != "R3"


# ── R4: No-position SELL guard ────────────────────────────────────────────────

def test_r4_sell_on_held_position_allowed():
    h = _holding("NVDA", shares=3.0)
    p = _portfolio(h)
    result = check_order("NVDA", "SELL", p, today=TODAY)
    assert result.allowed


def test_r4_sell_on_watchlist_only_ticker_blocked():
    p = _portfolio(watch_list=["PLTR"])
    result = check_order("PLTR", "SELL", p, today=TODAY)
    assert result == ComplianceResult(False, "R4", result.reason)
    assert "no open position" in result.reason


def test_r4_sell_on_unknown_ticker_blocked():
    p = _portfolio()
    result = check_order("MSFT", "SELL", p, today=TODAY)
    assert result == ComplianceResult(False, "R4", result.reason)


# ── R5: Position concentration cap ───────────────────────────────────────────

def test_r5_buy_blocked_when_at_cap():
    # Portfolio: $5k cash + NVDA 1sh @ $2000 = $7k total. NVDA = 28.6% — below 30%
    h = _holding("NVDA", entry=2_000.0, shares=1.0)
    p = _portfolio(h, cash=5_000.0)
    result = check_order("NVDA", "BUY", p, today=TODAY)
    assert result.allowed  # 28.6% < 30% — allowed

    # Now push it above 30 %: NVDA 1sh @ $2200, cash $5k → weight = 30.6%
    h2 = _holding("NVDA", entry=2_200.0, shares=1.0)
    p2 = _portfolio(h2, cash=5_000.0)
    result2 = check_order("NVDA", "BUY", p2, today=TODAY)
    assert result2 == ComplianceResult(False, "R5", result2.reason)
    assert "position cap" in result2.reason


def test_r5b_uses_market_value_when_prices_provided():
    # At cost basis: NVDA $200 × 1sh / ($5000 + $200) = 3.8% — passes
    # At market $2500: NVDA $2500 × 1sh / ($5000 + $2500) = 33% — blocked
    h = _holding("NVDA", entry=200.0, shares=1.0)
    p = _portfolio(h, cash=5_000.0)
    result = check_order("NVDA", "BUY", p, today=TODAY, prices={"NVDA": 2500.0})
    assert not result.allowed
    assert result.rule == "R5"
    assert "market value" in result.reason


def test_r5b_falls_back_to_cost_basis_when_prices_missing():
    # Same position — without prices kwarg, falls back to cost basis (3.8% < 30%)
    h = _holding("NVDA", entry=200.0, shares=1.0)
    p = _portfolio(h, cash=5_000.0)
    result = check_order("NVDA", "BUY", p, today=TODAY)
    assert result.allowed  # cost basis weight 3.8% < 30%


def test_r5_does_not_apply_to_sell():
    h = _holding("NVDA", entry=2_200.0, shares=5.0)
    p = _portfolio(h, cash=1_000.0)
    result = check_order("NVDA", "SELL", p, today=TODAY)
    assert result.allowed


def test_r5_skipped_when_no_existing_position():
    p = _portfolio(cash=10_000.0)
    result = check_order("NVDA", "BUY", p, today=TODAY)
    # No existing holding → R5 not triggered (no current weight to check)
    assert result.rule != "R5"


# ── R6: T+1 cash settlement ───────────────────────────────────────────────────

def test_r6_buy_blocked_when_notional_exceeds_settled_cash(tmp_path: Path):
    trade_log = tmp_path / "trade_log.jsonl"
    yesterday = TODAY - timedelta(days=1)
    # Simulate a SELL yesterday for $400
    entry = {
        "action": "SELL",
        "submitted_at": yesterday.isoformat() + "T10:00:00",
        "shares_executed": 2.0,
        "limit_price": 200.0,
    }
    trade_log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    p = _portfolio(cash=500.0)
    # Notional $200 but settled cash = $500 - $400 = $100 → block
    result = check_order(
        "NVDA", "BUY", p,
        today=TODAY,
        trade_log_path=trade_log,
        suggested_notional=200.0,
    )
    assert result == ComplianceResult(False, "R6", result.reason)
    assert "settled cash" in result.reason


def test_r6_buy_allowed_when_notional_within_settled_cash(tmp_path: Path):
    trade_log = tmp_path / "trade_log.jsonl"
    p = _portfolio(cash=1_000.0)
    result = check_order(
        "NVDA", "BUY", p,
        today=TODAY,
        trade_log_path=trade_log,
        suggested_notional=200.0,
    )
    assert result.allowed


def test_r6_skipped_when_no_trade_log_path():
    p = _portfolio(cash=100.0)
    result = check_order(
        "NVDA", "BUY", p,
        today=TODAY,
        suggested_notional=500.0,  # would exceed cash, but no path → skipped
    )
    # R6 not triggered without trade_log_path
    assert result.rule != "R6"


def test_r6_old_sell_does_not_block(tmp_path: Path):
    trade_log = tmp_path / "trade_log.jsonl"
    old_date = TODAY - timedelta(days=5)
    entry = {
        "action": "SELL",
        "submitted_at": old_date.isoformat() + "T10:00:00",
        "shares_executed": 10.0,
        "limit_price": 200.0,
    }
    trade_log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    p = _portfolio(cash=500.0)
    result = check_order(
        "NVDA", "BUY", p,
        today=TODAY,
        trade_log_path=trade_log,
        suggested_notional=400.0,
    )
    assert result.allowed


# ── is_blocked_ticker ─────────────────────────────────────────────────────────

def test_is_blocked_ticker_warrant_suffix_no_portfolio():
    assert is_blocked_ticker("GMEWS") is True
    assert is_blocked_ticker("NVDAW") is True


def test_is_blocked_ticker_normal_ticker_no_portfolio():
    assert is_blocked_ticker("NVDA") is False


def test_is_blocked_ticker_harvest_hold_with_portfolio():
    h = _holding("GME", role="tax-loss-harvest")
    p = _portfolio(h)
    assert is_blocked_ticker("GME", p) is True


def test_is_blocked_ticker_wash_sale_lockout_with_portfolio():
    h = _holding("GME", wash_sale_lockout_until=FUTURE)
    p = _portfolio(h)
    assert is_blocked_ticker("GME", p) is True


def test_is_blocked_ticker_expired_lockout_not_blocked():
    h = _holding("GME", wash_sale_lockout_until=PAST)
    p = _portfolio(h)
    assert is_blocked_ticker("GME", p) is False


# ── Rule priority (R3 before R2 before R1) ────────────────────────────────────

def test_r3_takes_priority_over_r2():
    h = _holding("GMEWS", entry=0.0, role="tax-loss-harvest")
    p = _portfolio(h)
    result = check_order("GMEWS", "BUY", p, today=TODAY)
    assert result.rule == "R3"


def test_r2_takes_priority_over_r1():
    h = _holding("GME", role="tax-loss-harvest", wash_sale_lockout_until=FUTURE)
    p = _portfolio(h)
    result = check_order("GME", "BUY", p, today=TODAY)
    assert result.rule == "R2"


# ── Loader: wash_sale_lockout_until is populated ──────────────────────────────

def test_loader_populates_wash_sale_lockout_until(tmp_path: Path):
    import json as _json
    from portfolio_lib.loader import load_portfolio

    data = {
        "holdings": [
            {
                "ticker": "GME",
                "entry": 57.58,
                "shares": 26.0,
                "role": "tax-loss-harvest",
                "wash_sale_lockout_until": "2026-06-19",
            }
        ],
        "watch_list": [],
        "targets": {},
        "strategy": "test",
    }
    path = tmp_path / "portfolio.json"
    path.write_text(_json.dumps(data), encoding="utf-8")
    port = load_portfolio(path)
    gme = next(h for h in port.holdings if h.ticker == "GME")
    assert gme.wash_sale_lockout_until == "2026-06-19"
