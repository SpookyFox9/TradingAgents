"""Integration test: mocked TradingAgentsGraph — no live LLM calls."""

from unittest.mock import MagicMock, patch

import pytest

from portfolio_lib.analyzer import analyze_ticker, TickerKind, TickerResult

_FIXTURE_STATE = {
    "company_of_interest": "NVDA",
    "trade_date": "2026-05-04",
    "market_report": "Market looks strong.",
    "sentiment_report": "Bullish sentiment.",
    "news_report": "NVDA announces new GPU — earnings beat.",
    "fundamentals_report": "Revenue up 80% YoY. Free cash flow positive.",
    "investment_plan": "Accumulate on dips. Target $220.",
    "trader_investment_plan": "Buy up to 5% position at market.",
    "investment_debate_state": {
        "bull_history": [],
        "bear_history": [],
        "history": [],
        "current_response": "",
        "judge_decision": "The bull case is compelling given strong fundamentals.",
    },
    "risk_debate_state": {
        "aggressive_history": [],
        "conservative_history": [],
        "neutral_history": [],
        "history": [],
        "judge_decision": "Moderate risk — position sizing appropriate.",
    },
    "final_trade_decision": "BUY — strong conviction based on fundamentals and technical setup.",
}


@patch("portfolio_lib.analyzer.TradingAgentsGraph", autospec=True)
def _make_ta(mock_graph_cls):
    ta = MagicMock()
    ta.propagate.return_value = (_FIXTURE_STATE, "BUY")
    return ta


def test_analyze_ticker_returns_result():
    ta = MagicMock()
    ta.propagate.return_value = (_FIXTURE_STATE, "BUY")

    result = analyze_ticker(
        ta=ta,
        ticker="NVDA",
        analysis_date="2026-05-04",
        kind=TickerKind.HOLDING,
        entry=100.0,
        shares=2.0,
        target=None,
    )

    assert isinstance(result, TickerResult)
    assert result.ticker == "NVDA"
    assert result.decision == "BUY"
    assert result.kind == TickerKind.HOLDING
    assert result.entry == 100.0


def test_analyze_ticker_captures_full_state():
    ta = MagicMock()
    ta.propagate.return_value = (_FIXTURE_STATE, "BUY")

    result = analyze_ticker(
        ta=ta,
        ticker="NVDA",
        analysis_date="2026-05-04",
        kind=TickerKind.HOLDING,
        entry=100.0,
        shares=2.0,
        target=None,
    )

    assert "80% YoY" in result.fundamentals_report
    assert "GPU" in result.news_report
    assert "bull case" in result.invest_judge_decision
    assert "Moderate risk" in result.risk_judge_decision
    assert "Accumulate" in result.investment_plan


def test_analyze_ticker_watchlist_kind():
    ta = MagicMock()
    ta.propagate.return_value = (_FIXTURE_STATE, "BUY")

    result = analyze_ticker(
        ta=ta,
        ticker="BRO",
        analysis_date="2026-05-04",
        kind=TickerKind.WATCHLIST,
        entry=None,
        shares=0.0,
        target=65.0,
    )

    assert result.kind == TickerKind.WATCHLIST
    assert result.target == pytest.approx(65.0)
    assert result.entry is None
