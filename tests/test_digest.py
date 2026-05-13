from pathlib import Path
from unittest.mock import patch

import pytest

from portfolio_lib.analyzer import TickerResult, TickerKind
from portfolio_lib.digest import write_digest


def _make_result(ticker, kind, decision, entry=100.0, shares=1.0, target=None, acquired_date=None):
    return TickerResult(
        ticker=ticker,
        kind=kind,
        entry=entry,
        shares=shares,
        target=target,
        acquired_date=acquired_date,
        decision=decision,
        market_report="market",
        sentiment_report="sentiment",
        news_report="news",
        fundamentals_report="fundamentals",
        investment_plan="invest",
        trader_investment_plan="trader",
        invest_judge_decision="judge",
        risk_judge_decision="risk",
        final_trade_decision=decision,
    )


@pytest.fixture
def results():
    return [
        _make_result("NVDA", TickerKind.HOLDING, "UNDERWEIGHT", entry=100.0, shares=2.0),
        _make_result("GME", TickerKind.HOLDING, "SELL", entry=50.0, shares=10.0),
        _make_result("BRO", TickerKind.WATCHLIST, "BUY", entry=None, shares=0.0, target=65.0),
    ]


@patch("portfolio_lib.digest.get_price", return_value=150.0)
def test_digest_creates_file(mock_price, tmp_path, results):
    digest_path = write_digest(results, tmp_path, "2026-05-04", [("GMEWS", "warrant")])
    assert digest_path.exists()


@patch("portfolio_lib.digest.get_price", return_value=150.0)
def test_digest_contains_all_tickers(mock_price, tmp_path, results):
    path = write_digest(results, tmp_path, "2026-05-04", [])
    content = path.read_text(encoding="utf-8")
    assert "NVDA" in content
    assert "GME" in content
    assert "BRO" in content


@patch("portfolio_lib.digest.get_price", return_value=150.0)
def test_digest_gme_intentional_hold_flagged(mock_price, tmp_path, results):
    path = write_digest(results, tmp_path, "2026-05-04", [])
    content = path.read_text(encoding="utf-8")
    assert "intentional hold" in content.lower()


@patch("portfolio_lib.digest.get_price", return_value=150.0)
def test_digest_skipped_section(mock_price, tmp_path, results):
    path = write_digest(results, tmp_path, "2026-05-04", [("GMEWS", "warrant")])
    content = path.read_text(encoding="utf-8")
    assert "GMEWS" in content
    assert "warrant" in content


@patch("portfolio_lib.digest.get_price", return_value=None)
def test_digest_handles_missing_price(mock_price, tmp_path, results):
    path = write_digest(results, tmp_path, "2026-05-04", [])
    content = path.read_text(encoding="utf-8")
    assert "n/a" in content


@patch("portfolio_lib.digest.get_price", return_value=150.0)
def test_digest_utf8_encoding(mock_price, tmp_path, results):
    path = write_digest(results, tmp_path, "2026-05-04", [])
    content = path.read_text(encoding="utf-8")
    assert "�" not in content  # no replacement characters (encoding bug)


@patch("portfolio_lib.digest.get_price", return_value=150.0)
def test_digest_filename(mock_price, tmp_path, results):
    path = write_digest(results, tmp_path, "2026-05-04", [])
    assert path.name == "2026-05-04_SUMMARY.md"


@patch("portfolio_lib.digest.get_price", return_value=150.0)
def test_digest_filename_with_timestamp(mock_price, tmp_path, results):
    path = write_digest(results, tmp_path, "2026-05-04", [], run_timestamp="2026-05-04_1430")
    assert path.name == "2026-05-04_1430_SUMMARY.md"


@patch("portfolio_lib.digest.get_price", return_value=150.0)
def test_digest_header_uses_analysis_date(mock_price, tmp_path, results):
    path = write_digest(results, tmp_path, "2026-05-04", [], run_timestamp="2026-05-04_1430")
    content = path.read_text(encoding="utf-8")
    assert "Portfolio Summary — 2026-05-04" in content
