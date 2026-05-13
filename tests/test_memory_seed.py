from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import date, timedelta
import json

from portfolio_lib.memory_seed import seed_memories, _DOCTRINE_SEEDS
from portfolio_lib.signal_log import record_decision, grade_open_signals


def _make_ta():
    ta = MagicMock()
    ta.bull_memory = MagicMock()
    ta.bear_memory = MagicMock()
    ta.trader_memory = MagicMock()
    ta.invest_judge_memory = MagicMock()
    ta.portfolio_manager_memory = MagicMock()
    return ta


def _backdated(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_seed_memories_calls_add_situations(tmp_path):
    ta = _make_ta()
    seed_memories(ta, tmp_path)
    ta.bull_memory.add_situations.assert_called()
    ta.bear_memory.add_situations.assert_called()
    ta.trader_memory.add_situations.assert_called()
    ta.portfolio_manager_memory.add_situations.assert_called()


def test_seed_memories_includes_doctrine(tmp_path):
    ta = _make_ta()
    seed_memories(ta, tmp_path)
    call_args = ta.bull_memory.add_situations.call_args_list[0][0][0]
    # Doctrine seeds have tuples of (situation, recommendation)
    assert len(call_args) == len(_DOCTRINE_SEEDS)
    assert isinstance(call_args[0], tuple)
    assert len(call_args[0]) == 2


def test_seed_memories_with_graded_signals(tmp_path):
    # Create a graded signal
    record_decision(tmp_path, "NVDA", _backdated(20), "BUY", 100.0)
    grade_open_signals(tmp_path, lambda t: 106.0)  # +6% → Correct

    ta = _make_ta()
    seed_memories(ta, tmp_path)

    # Should be called twice: once for doctrine, once for signals (both on bull_memory)
    assert ta.bull_memory.add_situations.call_count == 2


def test_seed_memories_no_log_file(tmp_path):
    ta = _make_ta()
    # No signal log exists — should not raise
    seed_memories(ta, tmp_path)
    ta.bull_memory.add_situations.assert_called()


def test_doctrine_seeds_format():
    for situation, recommendation in _DOCTRINE_SEEDS:
        assert isinstance(situation, str) and len(situation) > 10
        assert isinstance(recommendation, str) and len(recommendation) > 10
