from pathlib import Path
from datetime import date, timedelta

from portfolio_lib.memory_seed import build_doctrine_context, _DOCTRINE_SEEDS
from portfolio_lib.signal_log import record_decision, grade_open_signals


def _backdated(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_build_doctrine_context_no_signals(tmp_path):
    result = build_doctrine_context(tmp_path)
    assert isinstance(result, str)
    assert "Portfolio Strategy Rules" in result
    assert len(result) > 100


def test_build_doctrine_context_includes_all_doctrine(tmp_path):
    result = build_doctrine_context(tmp_path)
    for situation, rule in _DOCTRINE_SEEDS:
        assert situation[:40] in result
        assert rule[:40] in result


def test_build_doctrine_context_with_graded_signals(tmp_path):
    record_decision(tmp_path, "NVDA", _backdated(20), "BUY", 100.0)
    grade_open_signals(tmp_path, lambda t: 106.0)  # +6% → Correct

    result = build_doctrine_context(tmp_path)
    assert "Graded Past Signals" in result
    assert "NVDA" in result
    assert "Correct" in result or "★ Confirmed" in result


def test_build_doctrine_context_wrong_signal_marker(tmp_path):
    record_decision(tmp_path, "GME", _backdated(20), "BUY", 50.0)
    grade_open_signals(tmp_path, lambda t: 44.0)  # -12% → Wrong

    result = build_doctrine_context(tmp_path)
    assert "GME" in result
    assert "Wrong" in result or "✗ Wrong" in result


def test_build_doctrine_context_no_log_file(tmp_path):
    # No signal log exists — should return doctrine only, no error
    result = build_doctrine_context(tmp_path)
    assert "Portfolio Strategy Rules" in result


def test_doctrine_seeds_format():
    for situation, recommendation in _DOCTRINE_SEEDS:
        assert isinstance(situation, str) and len(situation) > 10
        assert isinstance(recommendation, str) and len(recommendation) > 10
